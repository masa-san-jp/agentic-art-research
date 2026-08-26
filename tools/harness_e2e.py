"""One-command request-to-verified-handoff orchestration.

This module is deliberately a composition layer.  Claiming, worker isolation,
acceptance, and task completion remain owned by their existing contracts; this
entry point only decides the phase order and the final output boundary.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import yaml

import build_handoff
import complete
import export_handoff
import harness_supervisor
import stopping_policy
import task_runtime
from _common import atomic_write_text, load_json, load_yaml, stable_json
from canonical import canonical_sha256
from harness_paths import HarnessPathError, HarnessPaths
from harness import HarnessError, bootstrap
from validate import validate_repository


STATE_RELATIVE = Path(".harness/e2e-run.json")
RUN_MANIFEST_RELATIVE = Path(".harness/run.json")
CHECKSUMS_SCHEMA = "harness-checksums"
OUTCOME_SCHEMA = "harness-outcome"
RUN_SCHEMA = "harness-run"
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$")


class HarnessE2EError(ValueError):
    """A named public E2E boundary failure."""

    def __init__(self, rule: str, message: str, *, phase: str = "PREFLIGHT") -> None:
        self.rule = rule
        self.phase = phase
        super().__init__(f"{rule}: {message}")


def _schema_validator(protocol_root: Path, name: str) -> Draft202012Validator:
    try:
        schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
        common = load_json(protocol_root / "schemas" / "common.schema.json")
        resources = [(common["$id"], Resource.from_contents(common))]
        for related in ("harness-outcome", "harness-run", "harness-checksums"):
            path = protocol_root / "schemas" / f"{related}.schema.json"
            if path.is_file():
                value = load_json(path)
                resources.append((value["$id"], Resource.from_contents(value)))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, registry=Registry().with_resources(resources))
    except Exception as exc:
        raise HarnessE2EError("HARNESS-PHASE", f"schema configuration is invalid: {name}: {exc}") from exc


def _validate_schema(protocol_root: Path, name: str, value: dict[str, Any]) -> None:
    errors = sorted(
        _schema_validator(protocol_root, name).iter_errors(value),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.absolute_path) or "$"
        raise HarnessE2EError("HARNESS-PHASE", f"{name}#{field}: {error.message}")


def _timestamp(value: str | None, fallback: str | None = None) -> str:
    candidate = value or fallback
    if candidate is None:
        raise HarnessE2EError("HARNESS-PHASE", "a deterministic RFC 3339 timestamp is required")
    if not TIMESTAMP_RE.fullmatch(candidate):
        raise HarnessE2EError("HARNESS-PHASE", f"invalid timestamp: {candidate!r}")
    return candidate


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _worker_fingerprint(adapter: str, command: Sequence[str] | None, fixture_mode: str | None) -> str:
    return canonical_sha256(
        {
            "adapter": adapter,
            "command": list(command) if command is not None else None,
            "fixture_mode": fixture_mode,
        }
    )


def _safe_copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise HarnessE2EError("HARNESS-OUTPUT-BOUNDARY", f"source is not a regular directory: {source}", phase="PUBLISHING")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise HarnessE2EError("HARNESS-OUTPUT-BOUNDARY", f"symlink cannot enter published output: {path}", phase="PUBLISHING")
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _file_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise HarnessE2EError("HARNESS-OUTPUT-BOUNDARY", f"published output contains a symlink: {path}", phase="PUBLISHING")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        entries.append({"path": relative, "sha256": _sha256_file(path), "size_bytes": path.stat().st_size})
    return entries


def _prefixed_entries(root: Path, directory: str) -> list[dict[str, Any]]:
    return [{**entry, "path": f"{directory}/{entry['path']}"} for entry in _file_entries(root / directory)]


def _task_counts(work_root: Path, project_id: str) -> dict[str, int]:
    try:
        tasks = list((task_runtime.load_runtime(work_root, project_id).get("tasks") or {}).values())
    except Exception:
        tasks = []
    statuses = {
        "total": len(tasks),
        "succeeded": 0,
        "pending": 0,
        "running": 0,
        "waiting_human": 0,
        "failed": 0,
        "blocked": 0,
    }
    for task in tasks:
        status = str(task.get("status", "")).lower()
        if status in statuses:
            statuses[status] += 1
    return statuses


def _resume_command(protocol_root: Path, work_root: Path, output_root: Path, run_id: str) -> str:
    return (
        f"python3 {protocol_root / 'tools' / 'harness.py'} resume"
        f" --protocol-root {protocol_root} --work-root {work_root}"
        f" --output-root {output_root} --run-id {run_id}"
    )


def _public_message(message: str, paths: HarnessPaths) -> str:
    rendered = str(message)
    for path, label in (
        (paths.protocol_root, "<protocol-root>"),
        (paths.work_root, "<work-root>"),
        (paths.output_root, "<output-root>"),
    ):
        rendered = rendered.replace(str(path), label)
    rendered = rendered.replace("PRIVATE_RAW", "[CLASSIFICATION]").replace("RESTRICTED", "[CLASSIFICATION]")
    return rendered


def _state_path(work_root: Path) -> Path:
    return work_root / STATE_RELATIVE


def _write_state(work_root: Path, value: dict[str, Any]) -> None:
    atomic_write_text(_state_path(work_root), stable_json(value))


def _load_state(work_root: Path) -> dict[str, Any] | None:
    path = _state_path(work_root)
    if not path.is_file():
        return None
    value = load_json(path)
    if not isinstance(value, dict):
        raise HarnessE2EError("HARNESS-RESUME", "E2E run state is not an object")
    return value


def _set_phase(state: dict[str, Any], work_root: Path, phase: str) -> None:
    state["phase"] = phase
    _write_state(work_root, state)


def _outcome_without_hash(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "outcome_sha256"}


def _with_outcome_hash(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["outcome_sha256"] = canonical_sha256(_outcome_without_hash(result))
    return result


def _validate_outcome(protocol_root: Path, value: dict[str, Any]) -> None:
    expected = canonical_sha256(_outcome_without_hash(value))
    if value.get("outcome_sha256") != expected:
        raise HarnessE2EError("HARNESS-PHASE", "outcome_sha256 does not match the canonical outcome")
    _validate_schema(protocol_root, OUTCOME_SCHEMA, value)


def _base_outcome(
    *,
    status: str,
    phase: str,
    run_id: str,
    project_id: str,
    request_sha256: str,
    protocol_commit: str,
    worker_fingerprint: str,
    counts: dict[str, int],
    output_path: str | None,
    resume_command: str | None,
    completion_status: str | None = None,
    handoff_id: str | None = None,
    handoff_sha256: str | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    paused: dict[str, Any] | None = None,
    failure: dict[str, str] | None = None,
) -> dict[str, Any]:
    return _with_outcome_hash(
        {
            "schema_version": "1.0.0",
            "outcome_sha256": "sha256:" + "0" * 64,
            "status": status,
            "phase": phase,
            "run_id": run_id,
            "project_id": project_id,
            "request_sha256": request_sha256,
            "protocol_commit": protocol_commit,
            "worker_fingerprint": worker_fingerprint,
            "task_counts": counts,
            "completion_status": completion_status,
            "handoff_id": handoff_id,
            "handoff_sha256": handoff_sha256,
            "artifacts": artifacts or [],
            "output_path": output_path,
            "resume_command": resume_command,
            "paused": paused,
            "failure": failure,
        }
    )


def _existing_output(
    *,
    protocol_root: Path,
    output_root: Path,
    slug: str,
    run_id: str,
    request_sha256: str,
    protocol_commit: str,
    worker_fingerprint: str,
) -> dict[str, Any] | None:
    target = output_root / slug
    if not target.exists():
        return None
    if target.is_symlink() or not target.is_dir():
        raise HarnessE2EError("HARNESS-OUTPUT-BOUNDARY", f"published project is not a regular directory: {target}")
    if {path.name for path in target.iterdir()} != {"research-project", "handoff", "run-manifest.json", "checksums.json"}:
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing output has an unexpected top-level object")
    manifest_path = target / "run-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing project has no trusted run-manifest.json")
    try:
        manifest = load_json(manifest_path)
    except Exception as exc:
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing run manifest cannot be parsed") from exc
    _validate_schema(protocol_root, RUN_SCHEMA, manifest)
    if manifest.get("run_id") != run_id:
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing output belongs to a different run ID")
    if any(
        manifest.get(key) != value
        for key, value in {
            "project_id": f"project/{slug}",
            "request_sha256": request_sha256,
            "protocol_commit": protocol_commit,
            "worker_fingerprint": worker_fingerprint,
        }.items()
    ):
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing output fingerprint differs; refusing overwrite")
    checksums_path = target / "checksums.json"
    if not checksums_path.is_file() or checksums_path.is_symlink():
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing project has no trusted checksums.json")
    checksums = load_json(checksums_path)
    _validate_schema(protocol_root, CHECKSUMS_SCHEMA, checksums)
    expected = {entry["path"]: entry["sha256"] for entry in checksums["entries"]}
    actual = {entry["path"]: entry["sha256"] for entry in _prefixed_entries(target, "research-project") + _prefixed_entries(target, "handoff")}
    if expected != actual:
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing output checksum differs; refusing overwrite")
    if checksums.get("file_set_sha256") != canonical_sha256(checksums["entries"]):
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "existing file-set checksum is invalid")
    return manifest


def _published_manifest(
    bootstrap_manifest: dict[str, Any],
    outcome: dict[str, Any],
    *,
    output_path: Path,
) -> dict[str, Any]:
    manifest = dict(bootstrap_manifest)
    manifest.update(
        {
            "status": outcome["status"],
            "phase": outcome["phase"],
            "project_path": "research-project",
            "output_path": str(output_path),
            "outcome_sha256": outcome["outcome_sha256"],
            "published_outcome_sha256": outcome["outcome_sha256"],
            "worker_fingerprint": outcome["worker_fingerprint"],
            "task_counts": outcome["task_counts"],
            "completion_status": outcome["completion_status"],
            "handoff_id": outcome["handoff_id"],
            "artifacts": outcome["artifacts"],
            "resume_command": outcome["resume_command"],
        }
    )
    return manifest


def _publish(
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
    bootstrap_manifest: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    slug = outcome["project_id"].split("/", 1)[1]
    target = output_root / slug
    if target.exists():
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "output appeared while publishing; refusing overwrite", phase="PUBLISHING")
    output_root.mkdir(parents=True, exist_ok=True)
    project = work_root / "projects" / slug
    staging = Path(tempfile.mkdtemp(prefix=f".{slug}.{outcome['run_id']}.", dir=output_root))
    try:
        _safe_copy_tree(project, staging / "research-project")
        try:
            export_handoff.export_handoff(
                work_root,
                outcome["project_id"],
                staging / "handoff",
                allow_dirty=True,
                protocol_root=protocol_root,
                work_root=work_root,
            )
        except Exception as exc:
            raise HarnessE2EError("HARNESS-HANDOFF", str(exc), phase="EXPORTING") from exc
        entries = _prefixed_entries(staging, "research-project") + _prefixed_entries(staging, "handoff")
        entries = sorted(entries, key=lambda item: item["path"])
        outcome["artifacts"] = entries
        handoff_file = staging / "research-project" / "05_production" / "production-handoff.yaml"
        outcome["handoff_sha256"] = _sha256_file(handoff_file)
        outcome["phase"] = "COMPLETE"
        outcome = _with_outcome_hash(outcome)
        checksums = {
            "schema_version": "1.0.0",
            "project_id": outcome["project_id"],
            "run_id": outcome["run_id"],
            "entries": entries,
            "file_set_sha256": canonical_sha256(entries),
        }
        _validate_schema(protocol_root, CHECKSUMS_SCHEMA, checksums)
        atomic_write_text(staging / "checksums.json", stable_json(checksums))
        published = _published_manifest(bootstrap_manifest, outcome, output_path=target)
        _validate_schema(protocol_root, RUN_SCHEMA, published)
        atomic_write_text(staging / "run-manifest.json", stable_json(published))
        top_level = {path.name for path in staging.iterdir()}
        if top_level != {"research-project", "handoff", "run-manifest.json", "checksums.json"}:
            raise HarnessE2EError("HARNESS-OUTPUT-BOUNDARY", "published output has an unexpected top-level object", phase="PUBLISHING")
        # The outcome hash is calculated after artifact hashes are known.  The
        # published manifest and stdout therefore carry the same immutable hash.
        _validate_outcome(protocol_root, outcome)
        os.replace(staging, target)
        return outcome
    except HarnessE2EError:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging)
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", str(exc), phase="PUBLISHING") from exc


def _outcome_from_manifest(
    protocol_root: Path,
    manifest: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    outcome = {
        "schema_version": "1.0.0",
        "outcome_sha256": manifest["outcome_sha256"],
        "status": "COMPLETE",
        "phase": "COMPLETE",
        "run_id": manifest["run_id"],
        "project_id": manifest["project_id"],
        "request_sha256": manifest["request_sha256"],
        "protocol_commit": manifest["protocol_commit"],
        "worker_fingerprint": manifest["worker_fingerprint"],
        "task_counts": manifest["task_counts"],
        "completion_status": manifest.get("completion_status"),
        "handoff_id": manifest.get("handoff_id"),
        "handoff_sha256": next(
            (item["sha256"] for item in manifest.get("artifacts", []) if item.get("path") == "research-project/05_production/production-handoff.yaml"),
            None,
        ),
        "artifacts": manifest.get("artifacts", []),
        "output_path": manifest.get("output_path"),
        "resume_command": manifest.get("resume_command"),
        "paused": None,
        "failure": None,
    }
    # The stored manifest is the source of truth for the original publish.  A
    # repeated invocation changes only the public status and gets a fresh
    # deterministic outcome hash; the artifact hashes never change.
    original_hash = manifest.get("published_outcome_sha256", outcome["outcome_sha256"])
    outcome["outcome_sha256"] = original_hash
    original = _with_outcome_hash(outcome)
    if original["outcome_sha256"] != original_hash:
        raise HarnessE2EError("HARNESS-PUBLISH-CONFLICT", "published run manifest outcome hash is invalid")
    outcome["status"] = status
    outcome = _with_outcome_hash(outcome)
    _validate_outcome(protocol_root, outcome)
    if status == "ALREADY_PUBLISHED":
        updated_manifest = dict(manifest)
        updated_manifest.update({"status": status, "outcome_sha256": outcome["outcome_sha256"]})
        _validate_schema(protocol_root, RUN_SCHEMA, updated_manifest)
        atomic_write_text(Path(str(manifest["output_path"])) / "run-manifest.json", stable_json(updated_manifest))
    return outcome


def _supervisor_failure(
    *,
    protocol_root: Path,
    paths: HarnessPaths,
    manifest: dict[str, Any],
    worker_fingerprint: str,
    journal: dict[str, Any] | None,
    rule: str,
    message: str,
    phase: str = "RUNNING",
) -> dict[str, Any]:
    project_id = manifest["project_id"]
    status = "BLOCKED" if rule in {"HARNESS-RESUME", "HARNESS-NO-PROGRESS"} else "FAILED"
    if journal and journal.get("status") == "NO_TASK_READY":
        status = "NO_TASK_READY"
    paused = journal.get("human_wait") if journal and journal.get("status") == "PAUSED" else None
    if paused:
        status = "PAUSED"
    return _with_outcome_hash(
        _base_outcome(
            status=status,
            phase=phase,
            run_id=manifest["run_id"],
            project_id=project_id,
            request_sha256=manifest["request_sha256"],
            protocol_commit=manifest["protocol_commit"],
            worker_fingerprint=worker_fingerprint,
            counts=_task_counts(paths.work_root, project_id),
            output_path=None,
            resume_command=_resume_command(paths.protocol_root, paths.work_root, paths.output_root, manifest["run_id"]),
            paused=paused,
            failure=None if paused else {"rule": rule, "message": _public_message(message, paths)},
        )
    )


def _load_bootstrap_manifest(work_root: Path) -> dict[str, Any]:
    path = work_root / RUN_MANIFEST_RELATIVE
    if not path.is_file() or path.is_symlink():
        raise HarnessE2EError("HARNESS-RESUME", "bootstrap run manifest is missing")
    value = load_json(path)
    if not isinstance(value, dict):
        raise HarnessE2EError("HARNESS-RESUME", "bootstrap run manifest is not an object")
    return value


def _set_handoff_mode(project: Path) -> bytes:
    manifest_path = project / "manifest.yaml"
    before = manifest_path.read_bytes()
    manifest = load_yaml(manifest_path) or {}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("project"), dict):
        raise HarnessE2EError("HARNESS-HANDOFF", "manifest cannot be promoted to handoff workflow", phase="BUILDING_HANDOFF")
    manifest["workflow_mode"] = "PRODUCTION_HANDOFF"
    entry_points = manifest.setdefault("entry_points", {})
    if isinstance(entry_points, dict):
        entry_points.update(
            {
                "production_hypotheses": "04_decisions/production-hypotheses.yaml",
                "hypothesis_comparison": "04_decisions/hypothesis-comparison.yaml",
                "prototype_plans": "05_production/prototype-plans.yaml",
                "production_handoff": "05_production/production-handoff.yaml",
                "production_change_requests": "06_governance/production-change-requests.yaml",
                "production_feedback_imports": "07_runtime/production-feedback-imports.jsonl",
            }
        )
    atomic_write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    return before


def run_request(
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
    run_id: str,
    request_path: Path | None = None,
    adapter: str = "fake",
    command: Sequence[str] | None = None,
    worker_id: str = "supervisor",
    fixture_mode: str | None = None,
    now: str | None = None,
    profiles_root: Path | None = None,
    art_history_root: Path | None = None,
    production_schema: Path | None = None,
    max_runtime_seconds: int | None = None,
    max_tasks: int | None = None,
    worker_runner: Callable[..., dict[str, Any]] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Run or resume one request and always return a versioned outcome."""

    protocol = protocol_root.resolve()
    work = work_root.resolve()
    output = output_root.resolve()
    paths = HarnessPaths.resolve(protocol, work, output)
    state_hint = _load_state(work) if resume and work.is_dir() else None
    if isinstance(state_hint, dict):
        # A resume command intentionally needs only run_id and roots.  The
        # original provider-neutral adapter configuration is hash-bound in the
        # work journal and is restored from there; callers cannot silently
        # resume a run with a different worker contract.
        adapter = str(state_hint.get("adapter", adapter))
        saved_command = state_hint.get("command")
        command = tuple(saved_command) if isinstance(saved_command, list) and all(isinstance(item, str) for item in saved_command) else command
        fixture_mode = state_hint.get("fixture_mode") if isinstance(state_hint.get("fixture_mode"), str) else fixture_mode
        worker_id = str(state_hint.get("worker_id", worker_id))
    timestamp = _timestamp(now, "2026-08-26T00:00:00+00:00")
    worker_fingerprint = _worker_fingerprint(adapter, command, fixture_mode)

    try:
        if not resume and request_path is not None:
            from accept_research_request import _validate_request
            import harness as harness_module

            request, request_sha256 = _validate_request(protocol, request_path.expanduser().resolve())
            slug = str(request["project"]["slug"])
            protocol_commit, _ = harness_module._git_head(protocol)
            existing = _existing_output(
                protocol_root=protocol,
                output_root=output,
                slug=slug,
                run_id=run_id,
                request_sha256=request_sha256,
                protocol_commit=protocol_commit,
                worker_fingerprint=worker_fingerprint,
            )
            if existing is not None:
                return _outcome_from_manifest(protocol, existing, status="ALREADY_PUBLISHED")
        if resume:
            manifest = _load_bootstrap_manifest(work)
            if manifest.get("run_id") != run_id:
                raise HarnessE2EError("HARNESS-RESUME", "run ID does not match the bootstrap manifest")
            existing = _existing_output(
                protocol_root=protocol,
                output_root=output,
                slug=manifest["project_id"].split("/", 1)[1],
                run_id=run_id,
                request_sha256=manifest["request_sha256"],
                protocol_commit=manifest["protocol_commit"],
                worker_fingerprint=worker_fingerprint,
            )
            if existing is not None:
                return _outcome_from_manifest(protocol, existing, status="ALREADY_PUBLISHED")
        else:
            manifest = bootstrap(
                protocol_root=protocol,
                work_root=work,
                output_root=output,
                run_id=run_id,
                now=timestamp,
                request_path=request_path,
                profiles_root=profiles_root,
                art_history_root=art_history_root,
                production_schema=production_schema,
            )
        state = _load_state(work) or {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "project_id": manifest["project_id"],
            "request_sha256": manifest["request_sha256"],
            "worker_fingerprint": worker_fingerprint,
            "phase": "BOOTSTRAPPED",
            "status": "RUNNING",
        }
        state.update(
            {
                "adapter": adapter,
                "command": list(command) if command is not None else None,
                "fixture_mode": fixture_mode,
                "worker_id": worker_id,
            }
        )
        if state.get("request_sha256") != manifest.get("request_sha256") or state.get("worker_fingerprint") != worker_fingerprint:
            raise HarnessE2EError("HARNESS-RESUME", "resume fingerprint differs from the original run")
        _set_phase(state, work, "BOOTSTRAPPED")

        supervisor = harness_supervisor.Supervisor(
            protocol_root=protocol,
            work_root=work,
            output_root=output,
            project_id=manifest["project_id"],
            run_id=run_id,
            worker_id=worker_id,
            adapter=adapter,
            command=command,
            fixture_mode=fixture_mode,
            max_runtime_seconds=max_runtime_seconds,
            max_tasks=max_tasks,
            now=lambda: timestamp,
            sleep=lambda _seconds: None,
            worker_runner=worker_runner,
        )
        _set_phase(state, work, "RUNNING")
        supervisor_journal = supervisor.run(resume=resume or (work / ".harness/supervisor").exists())
        if supervisor_journal.get("status") != "SUCCEEDED":
            outcome = _supervisor_failure(
                protocol_root=protocol,
                paths=paths,
                manifest=manifest,
                worker_fingerprint=worker_fingerprint,
                journal=supervisor_journal,
                rule=str((supervisor_journal.get("events") or [{}])[-1].get("failure_class") or "HARNESS-SUPERVISOR"),
                message=str((supervisor_journal.get("events") or [{}])[-1].get("failure_class") or "supervisor did not reach task completion"),
            )
            state["status"] = outcome["status"]
            _write_state(work, state)
            _validate_outcome(protocol, outcome)
            return outcome

        _set_phase(state, work, "COMPLETING")
        stopping_policy.apply_project(work, manifest["project_id"], evaluated_at=timestamp)
        findings = validate_repository(work, manifest["project_id"], protocol_root=protocol, work_root=work)
        if findings:
            raise HarnessE2EError("HARNESS-COMPLETION", "project validation failed before completion", phase="COMPLETING")
        completion_report = complete.complete_project(work, manifest["project_id"], completed_at=timestamp)
        if completion_report.get("status") in {"INCOMPLETE", "BLOCKED"}:
            raise HarnessE2EError("HARNESS-COMPLETION", f"completion status is {completion_report.get('status')}", phase="COMPLETING")

        _set_phase(state, work, "BUILDING_HANDOFF")
        project = work / "projects" / manifest["project_id"].split("/", 1)[1]
        before_manifest = _set_handoff_mode(project)
        try:
            handoff_path = build_handoff.build_handoff(
                work,
                manifest["project_id"],
                protocol_root=protocol,
                work_root=work,
                research_commit=manifest["protocol_commit"],
            )
        except Exception as exc:
            atomic_write_text(project / "manifest.yaml", before_manifest.decode("utf-8"))
            raise HarnessE2EError("HARNESS-HANDOFF", str(exc), phase="BUILDING_HANDOFF") from exc
        final_findings = validate_repository(work, manifest["project_id"], protocol_root=protocol, work_root=work)
        if final_findings:
            atomic_write_text(project / "manifest.yaml", before_manifest.decode("utf-8"))
            raise HarnessE2EError("HARNESS-HANDOFF", "built handoff failed project validation", phase="BUILDING_HANDOFF")
        handoff = load_yaml(handoff_path) or {}
        outcome = _base_outcome(
            status="COMPLETE",
            phase="EXPORTING",
            run_id=run_id,
            project_id=manifest["project_id"],
            request_sha256=manifest["request_sha256"],
            protocol_commit=manifest["protocol_commit"],
            worker_fingerprint=worker_fingerprint,
            counts=_task_counts(work, manifest["project_id"]),
            output_path=str(output / project.name),
            resume_command=_resume_command(protocol, work, output, run_id),
            completion_status=completion_report.get("status"),
            handoff_id=handoff.get("handoff_id"),
        )
        _set_phase(state, work, "EXPORTING")
        outcome = _publish(
            protocol_root=protocol,
            work_root=work,
            output_root=output,
            bootstrap_manifest=manifest,
            outcome=outcome,
        )
        _set_phase(state, work, "COMPLETE")
        state["status"] = outcome["status"]
        state["outcome_sha256"] = outcome["outcome_sha256"]
        _write_state(work, state)
        _validate_outcome(protocol, outcome)
        return outcome
    except (HarnessE2EError, HarnessError, HarnessPathError, harness_supervisor.SupervisorError, OSError, ValueError) as exc:
        if isinstance(exc, HarnessE2EError):
            rule, phase = exc.rule, exc.phase
        elif isinstance(exc, HarnessError):
            rule, phase = exc.rule, "PREFLIGHT"
        elif isinstance(exc, HarnessPathError):
            rule, phase = exc.args[0].split(":", 1)[0], "PREFLIGHT"
        elif isinstance(exc, harness_supervisor.SupervisorError):
            rule, phase = exc.rule, "RUNNING"
        else:
            rule, phase = "HARNESS-PHASE", "PREFLIGHT"
        manifest_path = work / RUN_MANIFEST_RELATIVE
        manifest = load_json(manifest_path) if manifest_path.is_file() else {
            "run_id": run_id,
            "project_id": "project/unknown",
            "request_sha256": "sha256:" + "0" * 64,
            "protocol_commit": "0" * 40,
        }
        project_id = manifest.get("project_id", "project/unknown")
        counts = _task_counts(work, project_id) if (work / "projects").is_dir() else {key: 0 for key in ("total", "succeeded", "pending", "running", "waiting_human", "failed", "blocked")}
        outcome = _base_outcome(
            status="BLOCKED" if rule in {"HARNESS-RESUME", "HARNESS-OUTPUT-BOUNDARY", "HARNESS-NO-PROGRESS"} else "FAILED",
            phase=phase,
            run_id=run_id,
            project_id=project_id,
            request_sha256=manifest.get("request_sha256", "sha256:" + "0" * 64),
            protocol_commit=manifest.get("protocol_commit", "0" * 40),
            worker_fingerprint=worker_fingerprint,
            counts=counts,
            output_path=None,
            resume_command=_resume_command(protocol, work, output, run_id),
            failure={"rule": rule if rule.startswith("HARNESS-") else "HARNESS-PHASE", "message": _public_message(str(exc), paths)},
        )
        if (work / "projects").is_dir():
            state = _load_state(work) or {"schema_version": "1.0.0", "run_id": run_id, "project_id": project_id}
            state.update({"phase": phase, "status": outcome["status"], "failure": outcome["failure"]})
            _write_state(work, state)
        _validate_outcome(protocol, outcome)
        return outcome


def resume_request(**kwargs: Any) -> dict[str, Any]:
    kwargs["resume"] = True
    kwargs.pop("request_path", None)
    return run_request(**kwargs)


__all__ = ["HarnessE2EError", "resume_request", "run_request"]
