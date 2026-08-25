#!/usr/bin/env python3
"""Bootstrap an isolated, resumable agent-harness run.

The command deliberately performs only initialization.  Worker execution,
write-target enforcement, acceptance execution, and supervision are later
harness milestones.  A successful bootstrap leaves project state in work_root
and leaves output_root empty.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import ROOT, atomic_write_text, load_json, load_yaml, stable_json
from accept_research_request import RequestAcceptanceError, accept_research_request
from canonical import canonical_sha256
from harness_paths import HarnessPathError, HarnessPaths, PROJECT_SLUG, ensure_empty_directory
import human_decisions
import harness_supervisor
from new_project import create_project
import task_runtime
from task_runtime import initialize_runtime
from validate import validate_repository


RUN_MANIFEST = Path(".harness/run.json")
RUN_ID = re.compile(r"^HR[0-9]{3,}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$")


class HarnessError(ValueError):
    """Raised when bootstrap cannot safely initialize a run."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(f"{rule}: {message}")


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    if not TIMESTAMP.fullmatch(value):
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", f"now must be RFC 3339 with timezone: {value!r}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", f"invalid now timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", f"now must include a timezone: {value!r}")
    return value


def _git_head(protocol_root: Path) -> tuple[str, bool]:
    try:
        head = subprocess.run(
            ["git", "-C", str(protocol_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(protocol_root), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HarnessError(
            "HARNESS-PROTOCOL-PROVENANCE",
            "protocol_root must be a Git checkout with a readable HEAD",
        ) from exc
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise HarnessError("HARNESS-PROTOCOL-PROVENANCE", "protocol HEAD is not a 40-character lowercase SHA")
    return head, not bool(status.strip())


def _optional_dependency(
    dependency_id: str,
    value: Path | None,
    *,
    kind: str,
) -> dict[str, Any]:
    if value is None:
        return {
            "id": dependency_id,
            "status": "MISSING",
            "required": False,
            "source": None,
            "message": "optional dependency was not supplied",
        }
    source = str(value.expanduser().resolve())
    if value.is_symlink():
        return {
            "id": dependency_id,
            "status": "INVALID",
            "required": True,
            "source": source,
            "message": "dependency path must not be a symbolic link",
        }
    expected_file = kind == "file"
    if (expected_file and not value.is_file()) or (not expected_file and not value.is_dir()):
        return {
            "id": dependency_id,
            "status": "INVALID",
            "required": True,
            "source": source,
            "message": "supplied dependency path has the wrong type or does not exist",
        }
    if dependency_id == "production-schema":
        try:
            document = load_json(value)
            Draft202012Validator.check_schema(document)
        except Exception as exc:
            return {
                "id": dependency_id,
                "status": "INVALID",
                "required": True,
                "source": source,
                "message": f"schema is not valid Draft 2020-12: {exc}",
            }
    return {
        "id": dependency_id,
        "status": "AVAILABLE",
        "required": True,
        "source": source,
        "message": "dependency is available",
    }


def _dependency_preflight(paths: HarnessPaths, profiles_root: Path | None, art_history_root: Path | None,
                          production_schema: Path | None) -> dict[str, Any]:
    checks = [
        {
            "id": "protocol-contract",
            "status": "AVAILABLE",
            "required": True,
            "source": str(paths.protocol_root),
            "message": "protocol config, schemas, templates, and tools are available",
        },
        _optional_dependency("profiles-root", profiles_root, kind="directory"),
        _optional_dependency("art-history-root", art_history_root, kind="directory"),
        _optional_dependency("production-schema", production_schema, kind="file"),
    ]
    blocked = any(item["required"] and item["status"] != "AVAILABLE" for item in checks)
    return {"status": "BLOCKED" if blocked else "READY", "checks": checks}


def _build_slug_input(slug: str, title: str, creator_id: str | None, now: str) -> dict[str, Any]:
    if not PROJECT_SLUG.fullmatch(slug):
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", "slug must be lower-case kebab-case")
    if not title.strip():
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", "title must not be empty")
    # This object is only the deterministic identity payload for the
    # slug/title entry point; it is not materialized as a research-request.
    return {
        "entry_point": "SLUG_TITLE",
        "requested_at": now,
        "project": {"slug": slug, "title": title, "creator_id": creator_id},
    }


def _copy_protocol_tree(protocol_root: Path, staging: Path) -> None:
    directories = ("config", "schemas", "templates", "tools", "docs", "profiles")
    files = ("AGENTS.md", "README.md", "PLANS.md", ".gitignore")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    for name in directories:
        source = protocol_root / name
        if source.is_symlink():
            raise HarnessError("HARNESS-PROTOCOL-PROVENANCE", f"protocol asset is a symbolic link: {source}")
        if not source.is_dir():
            raise HarnessError("HARNESS-PROTOCOL-PROVENANCE", f"protocol asset is missing: {source}")
        shutil.copytree(source, staging / name, symlinks=False, ignore=ignore)
    for name in files:
        source = protocol_root / name
        if source.is_symlink():
            raise HarnessError("HARNESS-PROTOCOL-PROVENANCE", f"protocol asset is a symbolic link: {source}")
        if source.is_file():
            shutil.copy2(source, staging / name)
    (staging / "projects").mkdir()
    (staging / "data").mkdir()


def _validate_run_manifest(protocol_root: Path, value: dict[str, Any]) -> None:
    try:
        schema = load_json(protocol_root / "schemas" / "harness-run.schema.json")
        common = load_json(protocol_root / "schemas" / "common.schema.json")
        registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
        validator = Draft202012Validator(schema, registry=registry)
        errors = sorted(validator.iter_errors(value), key=lambda error: (tuple(error.absolute_path), error.message))
    except Exception as exc:
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", f"harness-run schema configuration is invalid: {exc}") from exc
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.absolute_path) or "$"
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", f"harness-run#{field}: {error.message}")


def _existing_run(paths: HarnessPaths, run_id: str, request_sha256: str) -> dict[str, Any] | None:
    manifest = paths.work_root / RUN_MANIFEST
    if not manifest.exists():
        return None
    if manifest.is_symlink() or not manifest.is_file():
        raise HarnessError("HARNESS-BOOTSTRAP-CONFLICT", f"run manifest is not a regular file: {manifest}")
    try:
        value = load_json(manifest)
    except Exception as exc:
        raise HarnessError("HARNESS-BOOTSTRAP-CONFLICT", f"existing run manifest cannot be read: {exc}") from exc
    if not isinstance(value, dict):
        raise HarnessError("HARNESS-BOOTSTRAP-CONFLICT", "existing run manifest is not an object")
    if value.get("run_id") == run_id and value.get("request_sha256") == request_sha256:
        if value.get("work_root") != str(paths.work_root) or value.get("output_root") != str(paths.output_root):
            raise HarnessError("HARNESS-BOOTSTRAP-CONFLICT", "same run_id/request was initialized with different roots")
        _validate_run_manifest(paths.protocol_root, value)
        return value
    raise HarnessError(
        "HARNESS-BOOTSTRAP-CONFLICT",
        f"work_root already contains run {value.get('run_id')!r}; it cannot be replaced by {run_id!r}",
    )


def bootstrap(
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
    run_id: str,
    now: str | None = None,
    request_path: Path | None = None,
    slug: str | None = None,
    title: str | None = None,
    creator_id: str | None = None,
    profiles_root: Path | None = None,
    art_history_root: Path | None = None,
    production_schema: Path | None = None,
) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id):
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", "run_id must match HR followed by at least three digits")
    if (request_path is None) == (slug is None):
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", "supply exactly one of request or slug/title")
    if slug is not None and title is None:
        raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", "--title is required with --slug")
    if request_path is not None and request_path.is_symlink():
        raise HarnessError("HARNESS-ROOT-BOUNDARY", f"request input must not be a symbolic link: {request_path}")

    paths = HarnessPaths.resolve(protocol_root, work_root, output_root)
    timestamp = _timestamp(now)
    protocol_commit, protocol_clean = _git_head(paths.protocol_root)
    preflight = _dependency_preflight(paths, profiles_root, art_history_root, production_schema)
    if preflight["status"] == "BLOCKED":
        raise HarnessError("HARNESS-DEPENDENCY-PREFLIGHT", stable_json(preflight).strip())

    if request_path is not None:
        input_path = request_path.expanduser().resolve()
        if not input_path.is_file():
            raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", f"request input not found: {input_path}")
        # Validation and hashing happen before any work/output mutation.
        from accept_research_request import _validate_request

        request, request_sha256 = _validate_request(paths.protocol_root, input_path)
        request_kind = "RESEARCH_REQUEST"
        project_slug = str(request["project"]["slug"])
    else:
        assert slug is not None and title is not None
        identity = _build_slug_input(slug, title, creator_id, timestamp)
        request_sha256 = canonical_sha256(identity)
        request_kind = "SLUG_TITLE"
        project_slug = slug

    existing = _existing_run(paths, run_id, request_sha256)
    if existing is not None:
        return existing

    # Output is never initialized by bootstrap.  Both roots are checked before
    # the staging copy so a conflict leaves the caller's directories unchanged.
    ensure_empty_directory(paths.work_root, "work_root")
    ensure_empty_directory(paths.output_root, "output_root")
    staging = Path(tempfile.mkdtemp(prefix=f".{paths.work_root.name}.", dir=paths.work_root.parent))
    try:
        _copy_protocol_tree(paths.protocol_root, staging)
        if request_path is not None:
            summary = accept_research_request(
                staging,
                input_path,
                apply=True,
                accepted_at=timestamp,
                protocol_root=paths.protocol_root,
            )
            project_id = str(summary["project_id"])
        else:
            project = create_project(
                staging,
                project_slug,
                title or "",
                creator_id,
                created_at=timestamp,
                protocol_root=paths.protocol_root,
            )
            project_id = f"project/{project_slug}"
        initialize_runtime(staging, project_id, initialized_at=timestamp)
        findings = validate_repository(staging, project_id, protocol_root=paths.protocol_root)
        if findings:
            rendered = "\n".join(finding.render() for finding in findings)
            raise HarnessError("HARNESS-BOOTSTRAP-CONTRACT", f"initialized project failed validation:\n{rendered}")

        result: dict[str, Any] = {
            "schema_version": "1.0.0",
            "status": "BOOTSTRAPPED",
            "run_id": run_id,
            "request_kind": request_kind,
            "request_sha256": request_sha256,
            "project_id": project_id,
            "project_path": str(paths.work_root / "projects" / project_slug),
            "protocol_root": str(paths.protocol_root),
            "protocol_commit": protocol_commit,
            "protocol_tree_clean": protocol_clean,
            "work_root": str(paths.work_root),
            "output_root": str(paths.output_root),
            "initialized_at": timestamp,
            "dependency_preflight": preflight,
        }
        _validate_run_manifest(paths.protocol_root, result)
        atomic_write_text(staging / RUN_MANIFEST, stable_json(result))

        # Publish only after all project, runtime, validation, and manifest
        # work has succeeded.  The caller supplied an empty directory; rmdir
        # is intentionally limited to that exact validated target.
        if any(paths.work_root.iterdir()):
            raise HarnessError("HARNESS-BOOTSTRAP-CONFLICT", "work_root changed while bootstrap was staging")
        paths.work_root.rmdir()
        os.replace(staging, paths.work_root)
        return result
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def record_attempt_result(
    *,
    protocol_root: Path,
    work_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    worker_id: str,
    lease_token: str,
    result: dict[str, Any],
    now: str,
) -> dict[str, Any] | None:
    """Persist a HUMAN_REQUIRED worker result and release its task lease."""

    if result.get("status") != "HUMAN_REQUIRED":
        return None
    request = result.get("human_decision_request")
    if not isinstance(request, dict):
        raise HarnessError("HUMAN-DECISION-CATEGORY", "HUMAN_REQUIRED result has no typed decision request")
    try:
        return human_decisions.record_human_required(
            protocol_root=protocol_root,
            work_root=work_root,
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            worker_id=worker_id,
            lease_token=lease_token,
            result_request=request,
            now=now,
        )
    except (human_decisions.HumanDecisionError, task_runtime.TaskRuntimeError) as exc:
        raise HarnessError(getattr(exc, "rule", "HUMAN-DECISION-STATE"), str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap an isolated agent-harness run or manage human decisions.")
    parser.add_argument("command", choices=["bootstrap", "decisions", "run", "resume"])
    parser.add_argument("decision_command", nargs="?")
    parser.add_argument("target", nargs="?")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--request", type=Path, help="versioned research-request YAML/JSON")
    source.add_argument("--slug", help="project slug for the minimal slug/title entry point")
    parser.add_argument("--title", help="project title; required with --slug")
    parser.add_argument("--creator-id")
    parser.add_argument("--protocol-root", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-id")
    parser.add_argument("--now", help="RFC 3339 initialization timestamp")
    parser.add_argument("--profiles-root", type=Path)
    parser.add_argument("--art-history-root", type=Path)
    parser.add_argument("--production-schema", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--worker", default="supervisor")
    parser.add_argument("--adapter", default="fake")
    parser.add_argument("--command-json")
    parser.add_argument("--fixture-mode")
    parser.add_argument("--max-runtime-seconds", type=int)
    parser.add_argument("--max-tasks", type=int)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            if args.request is None and args.slug is None or args.request is not None and args.slug is not None:
                parser.error("bootstrap requires exactly one of --request or --slug")
            if not all((args.protocol_root, args.work_root, args.output_root, args.run_id)):
                parser.error("bootstrap requires --protocol-root, --work-root, --output-root, and --run-id")
            result = bootstrap(
                protocol_root=args.protocol_root,
                work_root=args.work_root,
                output_root=args.output_root,
                run_id=args.run_id,
                now=args.now,
                request_path=args.request,
                slug=args.slug,
                title=args.title,
                creator_id=args.creator_id,
                profiles_root=args.profiles_root,
                art_history_root=args.art_history_root,
                production_schema=args.production_schema,
            )
        elif args.command == "decisions":
            if args.decision_command not in {"list", "resolve"} or not args.target:
                parser.error("decisions requires list|resolve and project/<slug>")
            protocol_root = (args.protocol_root or args.root).resolve()
            work_root = (args.work_root or args.root).resolve()
            if args.decision_command == "list":
                result = {"project_id": args.target, "requests": human_decisions.unresolved_requests(work_root=work_root, project_id=args.target)}
            else:
                if args.response is None:
                    parser.error("decisions resolve requires --response")
                result = human_decisions.resolve_request(
                    protocol_root=protocol_root,
                    work_root=work_root,
                    project_id=args.target,
                    response=load_json(args.response),
                )
        else:
            target = args.decision_command
            if not target or not target.startswith("project/"):
                parser.error(f"{args.command} requires project/<slug>")
            if not all((args.protocol_root, args.work_root, args.output_root, args.run_id)):
                parser.error(f"{args.command} requires --protocol-root, --work-root, --output-root, and --run-id")
            command = None
            if args.command_json is not None:
                try:
                    command = json.loads(args.command_json)
                except json.JSONDecodeError as exc:
                    parser.error(f"--command-json must be a JSON argv array: {exc}")
                if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
                    parser.error("--command-json must be a JSON argv array")
            supervisor = harness_supervisor.Supervisor(
                protocol_root=args.protocol_root,
                work_root=args.work_root,
                output_root=args.output_root,
                project_id=target,
                run_id=args.run_id,
                worker_id=args.worker,
                adapter=args.adapter,
                command=command,
                fixture_mode=args.fixture_mode,
                max_runtime_seconds=args.max_runtime_seconds,
                max_tasks=args.max_tasks,
            )
            result = supervisor.run(resume=args.command == "resume")
    except (HarnessError, HarnessPathError, RequestAcceptanceError, human_decisions.HumanDecisionError, harness_supervisor.SupervisorError, task_runtime.TaskRuntimeError, OSError, ValueError) as exc:
        print(f"FAILED: {exc}")
        return 1
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
