"""Execute typed acceptance gates and complete one task through the harness."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from _common import atomic_write_text, load_json, load_yaml, read_jsonl, stable_json
from attempt_workspace import (
    AttemptWorkspace,
    AttemptWorkspaceError,
    inspect_attempt,
    manifest_sha256,
    promote_attempt,
    snapshot_project,
)
from canonical import canonical_sha256
from validate import validate_repository

import build_graph
import complete
import stopping_policy
import task_runtime


CHECK_KINDS = {
    "repository_validate",
    "project_validate",
    "collection_minimum",
    "run_event_minimum",
    "stopping_evaluate",
    "hypothesis_selection",
    "medium_decision",
    "graph_current",
    "completion_status",
}


class AcceptanceExecutorError(ValueError):
    """A typed acceptance gate or transaction cannot be safely completed."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "evaluated_at must be RFC 3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "evaluated_at must include a timezone")
    return value


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "check path must be a relative POSIX path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/")):
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "check path must stay inside the attempt project")
    return value.replace("\\", "/")


def _schema_validator(protocol_root: Path, name: str):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    registry = Registry().with_resources([(common["$id"], Resource.from_contents(common))])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate_schema(protocol_root: Path, name: str, value: dict[str, Any]) -> None:
    if any(_schema_validator(protocol_root, name).iter_errors(value)):
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", f"{name} contract is invalid")


def load_acceptance_checks(protocol_root: Path, role: str) -> list[dict[str, Any]]:
    try:
        config = load_yaml(protocol_root / "config" / "task-roles.yaml") or {}
    except Exception as exc:
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "task role configuration cannot be read") from exc
    role_entry = (config.get("roles") or {}).get(role) if isinstance(config, dict) else None
    if not isinstance(role_entry, dict):
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "acceptance role is not configured")
    if "acceptance" in role_entry:
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "legacy shell acceptance is forbidden")
    raw_checks = role_entry.get("acceptance_checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "acceptance_checks must be a non-empty list")
    checks: list[dict[str, Any]] = []
    for raw in raw_checks:
        if not isinstance(raw, dict):
            raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "acceptance check must be an object")
        check = dict(raw)
        kind = check.get("kind")
        if kind not in CHECK_KINDS:
            raise AcceptanceExecutorError("ACCEPTANCE-CHECK-UNKNOWN", "acceptance check kind is not supported")
        if "path" in check:
            check["path"] = _safe_relative(check["path"])
        try:
            _validate_schema(protocol_root, "acceptance-gate", check)
        except AcceptanceExecutorError:
            raise
        checks.append(check)
    ids = [str(check.get("id")) for check in checks]
    if len(ids) != len(set(ids)):
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "acceptance check IDs must be unique")
    return sorted(checks, key=lambda item: item["id"])


def _project_id_path(project_id: str) -> str:
    if not isinstance(project_id, str) or not project_id.startswith("project/") or project_id.count("/") != 1:
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "project_id must be project/<slug>")
    slug = project_id.split("/", 1)[1]
    if not slug or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in slug):
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "project_id contains an unsafe slug")
    return slug


def _validation_root(protocol_root: Path, attempt_project: Path, project_id: str) -> tuple[Path, str]:
    slug = _project_id_path(project_id)
    root = Path(tempfile.mkdtemp(prefix=".acceptance-validation-", dir=attempt_project.parent))
    try:
        (root / "projects").mkdir()
        shutil.copytree(attempt_project, root / "projects" / slug, symlinks=False)
        # Stopping and completion evaluators load policy from their supplied
        # root.  Copy only protocol configuration into the ephemeral root;
        # schemas and validator logic remain anchored at protocol_root.
        shutil.copytree(protocol_root / "config", root / "config", symlinks=False)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise AcceptanceExecutorError("ACCEPTANCE-GATE-FAILED", "attempt project could not be staged for validation")
    return root, project_id


def _load_collection(path: Path, key: str | None) -> tuple[int, Any]:
    if not path.is_file():
        raise AcceptanceExecutorError("ACCEPTANCE-GATE-FAILED", "acceptance target is missing")
    if path.suffix == ".jsonl":
        records = read_jsonl(path)
        return len(records), records
    value = load_yaml(path)
    selected = value.get(key) if key and isinstance(value, dict) else value
    if not isinstance(selected, list):
        raise AcceptanceExecutorError("ACCEPTANCE-GATE-FAILED", "acceptance target collection is missing")
    return len(selected), selected


def _gate_result(check: dict[str, Any], *, passed: bool, expected: Any, actual: Any, path: str | None = None, rule: str | None = None) -> dict[str, Any]:
    return {
        "id": check["id"],
        "kind": check["kind"],
        "status": "PASS" if passed else "FAIL",
        "path": path,
        "expected": expected,
        "actual": actual,
        "rule": None if passed else (rule or "ACCEPTANCE-GATE-FAILED"),
        "remediation": None if passed else "Correct the attempt workspace and rerun the typed acceptance gates.",
        "duration_ms": 0,
    }


def _run_one(check: dict[str, Any], *, protocol_root: Path, validation_root: Path, project_id: str) -> dict[str, Any]:
    kind = check["kind"]
    path_value = check.get("path")
    target_path = validation_root / "projects" / _project_id_path(project_id) / path_value if path_value else None
    if target_path is not None and not target_path.resolve().is_relative_to((validation_root / "projects" / _project_id_path(project_id)).resolve()):
        raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "acceptance target escapes the attempt project")
    try:
        if kind in {"repository_validate", "project_validate"}:
            findings = validate_repository(validation_root, project_id, protocol_root=protocol_root, work_root=validation_root)
            return _gate_result(check, passed=not findings, expected=0, actual=len(findings), path=None, rule="ACCEPTANCE-GATE-FAILED")
        if kind == "collection_minimum":
            count, _ = _load_collection(target_path, check.get("collection_key"))
            minimum = int(check.get("minimum", 1))
            return _gate_result(check, passed=count >= minimum, expected=minimum, actual=count, path=path_value)
        if kind == "run_event_minimum":
            records = read_jsonl(target_path)
            event_type = check.get("event_type")
            count = sum(1 for record in records if record.get("event_type") == event_type)
            minimum = int(check.get("minimum", 1))
            return _gate_result(check, passed=count >= minimum, expected={"event_type": event_type, "minimum": minimum}, actual=count, path=path_value)
        if kind == "stopping_evaluate":
            evaluation = stopping_policy.evaluate_project(validation_root, project_id)
            return _gate_result(check, passed=isinstance(evaluation, dict), expected="evaluation", actual=evaluation.get("status") if isinstance(evaluation, dict) else None)
        if kind == "hypothesis_selection":
            value = load_yaml(validation_root / "projects" / _project_id_path(project_id) / "04_decisions/hypothesis-comparison.yaml") or {}
            comparisons = value.get("comparisons", []) if isinstance(value, dict) else []
            complete = [item for item in comparisons if isinstance(item, dict) and item.get("status") == "COMPLETE"]
            recommended = [option for item in complete for option in item.get("options", []) if isinstance(option, dict) and option.get("recommendation") == "RECOMMENDED"]
            passed = bool(complete) and len(recommended) == 1
            return _gate_result(check, passed=passed, expected={"complete": 1, "recommended": 1}, actual={"complete": len(complete), "recommended": len(recommended)}, path="04_decisions/hypothesis-comparison.yaml")
        if kind == "medium_decision":
            value = load_yaml(validation_root / "projects" / _project_id_path(project_id) / "04_decisions/decision-log.yaml") or {}
            decisions = value.get("decisions", []) if isinstance(value, dict) else []
            medium = [item for item in decisions if isinstance(item, dict) and ("medium" in str(item.get("question", "")).lower() or "媒体" in str(item.get("question", "")) or "medium" in str(item.get("selected_option", "")).lower())]
            passed = bool(medium) and all(item.get("rejected_options") for item in medium)
            return _gate_result(check, passed=passed, expected="one adopted medium decision with rejected options", actual=len(medium), path="04_decisions/decision-log.yaml")
        if kind == "graph_current":
            graph = build_graph.build_graph(validation_root, protocol_root=protocol_root, work_root=validation_root)
            return _gate_result(check, passed=isinstance(graph, dict), expected="graph", actual=len(graph.get("nodes", [])) if isinstance(graph, dict) else None)
        if kind == "completion_status":
            evaluation = complete.evaluate_project(validation_root, project_id)
            expected = check.get("expected")
            actual = evaluation.get("status") if isinstance(evaluation, dict) else None
            passed = isinstance(actual, str) and (
                actual == expected
                if expected is not None
                else actual in {"COMPLETE", "COMPLETE_WITH_GAPS"}
            )
            return _gate_result(check, passed=passed, expected=expected or ["COMPLETE", "COMPLETE_WITH_GAPS"], actual=actual)
    except AcceptanceExecutorError:
        raise
    except Exception:
        return _gate_result(check, passed=False, expected=check.get("expected", "gate success"), actual="execution error", path=path_value)
    raise AcceptanceExecutorError("ACCEPTANCE-CHECK-UNKNOWN", "acceptance check kind is not supported")


def execute_acceptance(
    *,
    protocol_root: Path,
    attempt_project: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    role: str,
    evaluated_at: str,
    checks: Sequence[dict[str, Any]] | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Execute the role's typed checks against an isolated attempt project."""

    timestamp = _timestamp(evaluated_at)
    selected = list(checks) if checks is not None else load_acceptance_checks(protocol_root.resolve(), role)
    validation_root, target = _validation_root(protocol_root.resolve(), attempt_project.resolve(), project_id)
    try:
        gates: list[dict[str, Any]] = []
        for check in sorted(selected, key=lambda item: str(item.get("id"))):
            if not isinstance(check, dict) or not isinstance(check.get("id"), str) or not isinstance(check.get("kind"), str):
                raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "acceptance check must contain typed id and kind")
            # next_action carries roots beside the gate for an external
            # runner.  They are execution metadata, not part of the
            # versioned gate contract.
            gate = {
                key: value
                for key, value in check.items()
                if key not in {"project_id", "protocol_root", "work_root", "output_root"}
            }
            if gate.get("kind") not in CHECK_KINDS:
                raise AcceptanceExecutorError("ACCEPTANCE-CHECK-UNKNOWN", "acceptance check kind is not supported")
            _validate_schema(protocol_root.resolve(), "acceptance-gate", gate)
            gates.append(_run_one(gate, protocol_root=protocol_root.resolve(), validation_root=validation_root, project_id=target))
        status = "PASS" if all(gate["status"] == "PASS" for gate in gates) else "FAIL"
        payload = {
            "schema_version": "1.0.0",
            "project_id": project_id,
            "run_id": run_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "evaluated_at": timestamp,
            "status": status,
            "gates": gates,
        }
        payload["report_id"] = "AR-" + hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()[:16]
        _validate_schema(protocol_root.resolve(), "acceptance-report", payload)
        if report_path is not None:
            content = stable_json(payload)
            if report_path.exists():
                if report_path.read_bytes() != content.encode("utf-8"):
                    raise AcceptanceExecutorError("ACCEPTANCE-CHECK-SCHEMA", "existing acceptance report differs")
            else:
                atomic_write_text(report_path, content)
        return payload
    finally:
        shutil.rmtree(validation_root, ignore_errors=True)


run_acceptance = execute_acceptance


def _capture_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    captured: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            captured[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mode)
    return captured


def _restore_tree(root: Path, captured: dict[str, tuple[bytes, int]]) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for relative, (content, mode) in captured.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)


def _persist_tree(root: Path, captured: dict[str, tuple[bytes, int]]) -> None:
    """Persist the pre-promotion image so a crashed process can recover."""

    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for relative, (content, mode) in captured.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)


def _load_persisted_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    if not root.is_dir():
        raise AcceptanceExecutorError("ACCEPTANCE-ROLLBACK", "transaction preimage is missing")
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mode)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def _write_transaction_journal(
    attempt: AttemptWorkspace,
    *,
    protocol_root: Path,
    evaluated_at: str,
    report: dict[str, Any],
    report_hash: str,
    stage: str,
    changeset_hash: str | None,
) -> dict[str, Any]:
    transaction_id = "TX-" + hashlib.sha256(
        f"{attempt.project_id}|{attempt.run_id}|{attempt.task_id}|{attempt.attempt_id}".encode("utf-8")
    ).hexdigest()[:16]
    journal = {
        "schema_version": "1.0.0",
        "transaction_id": transaction_id,
        "project_id": attempt.project_id,
        "run_id": attempt.run_id,
        "task_id": attempt.task_id,
        "attempt_id": attempt.attempt_id,
        "report_id": report["report_id"],
        "report_sha256": report_hash,
        "changeset_sha256": changeset_hash,
        "stage": stage,
        "updated_at": evaluated_at,
    }
    _validate_schema(protocol_root.resolve(), "acceptance-transaction", journal)
    atomic_write_text(attempt.root / "transaction.json", stable_json(journal))
    return journal


def recover_attempt_transaction(
    attempt: AttemptWorkspace,
    *,
    protocol_root: Path,
    work_root: Path,
) -> dict[str, Any] | None:
    """Recover a process interrupted after promotion and before commit."""

    journal_path = attempt.root / "transaction.json"
    if not journal_path.is_file():
        return None
    journal = load_json(journal_path)
    _validate_schema(protocol_root.resolve(), "acceptance-transaction", journal)
    if journal["stage"] in {"COMMITTED", "ROLLED_BACK"}:
        return journal
    project = work_root.resolve() / "projects" / attempt.project_id.split("/", 1)[1]
    before = _load_persisted_tree(attempt.root / "transaction-before")
    # PREPARED can mean either "promotion never started" or a crash just
    # after the candidate swap.  Compare the canonical tree with the durable
    # changeset when available before deciding whether a restore is needed.
    current_manifest = snapshot_project(
        project,
        project_id=attempt.project_id,
        run_id=attempt.run_id,
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        role=attempt.role,
        protocol_root=protocol_root.resolve(),
        reject_hardlinks=False,
    )
    current_hash = manifest_sha256(current_manifest)
    baseline_hash = manifest_sha256(load_json(attempt.baseline_manifest))
    should_restore = journal["stage"] == "PROMOTED"
    if journal["stage"] == "PREPARED" and attempt.changeset.is_file():
        changeset = load_json(attempt.changeset)
        after_hash = changeset.get("after_manifest_sha256")
        if current_hash == after_hash:
            should_restore = True
        elif current_hash == baseline_hash:
            should_restore = False
        else:
            raise AcceptanceExecutorError("ACCEPTANCE-PROMOTION-CONFLICT", "recovery found an unrelated canonical project change")
    if journal["stage"] == "PROMOTED" and attempt.changeset.is_file():
        after_hash = load_json(attempt.changeset).get("after_manifest_sha256")
        if current_hash not in {after_hash, baseline_hash}:
            raise AcceptanceExecutorError("ACCEPTANCE-PROMOTION-CONFLICT", "recovery found an unrelated canonical project change")
    if should_restore:
        _restore_tree(project, before)
    journal["stage"] = "ROLLED_BACK"
    journal["updated_at"] = journal["updated_at"]
    atomic_write_text(journal_path, stable_json(journal))
    return journal


def complete_attempt(
    attempt: AttemptWorkspace,
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
    worker_id: str,
    lease_token: str,
    evaluated_at: str,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Run gates, promote, validate, and complete under one harness transaction."""

    report = execute_acceptance(
        protocol_root=protocol_root,
        attempt_project=attempt.project,
        project_id=attempt.project_id,
        run_id=attempt.run_id,
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        role=attempt.role,
        evaluated_at=evaluated_at,
        report_path=report_path or attempt.root / "acceptance-report.json",
    )
    report_hash = canonical_sha256(report)
    if report["status"] != "PASS":
        task_result = task_runtime.fail(
            work_root.resolve(),
            attempt.project_id,
            attempt.task_id,
            worker_id,
            lease_token,
            "VALIDATION",
            "Typed acceptance gate failed.",
            now=evaluated_at,
        )
        return {"report": report, "report_sha256": report_hash, "task": task_result, "promoted": False}

    # A retried delivery of the same successful attempt must not try to
    # promote against the already-changed canonical baseline.  The task
    # result and changeset are the durable idempotency record.
    runtime = task_runtime.load_runtime(work_root.resolve(), attempt.project_id)
    existing_task = (runtime.get("tasks") or {}).get(attempt.task_id)
    if isinstance(existing_task, dict) and existing_task.get("status") == "SUCCEEDED":
        existing_result = existing_task.get("result")
        if not isinstance(existing_result, dict) or existing_result.get("report_sha256") != report_hash:
            raise AcceptanceExecutorError("ACCEPTANCE-PROMOTION-CONFLICT", "task already succeeded with a different acceptance report")
        if not attempt.changeset.is_file():
            raise AcceptanceExecutorError("ACCEPTANCE-PROMOTION-CONFLICT", "completed task has no durable changeset")
        changeset = load_json(attempt.changeset)
        changeset_hash = canonical_sha256(changeset)
        effect_key = f"acceptance/{report_hash}/{changeset_hash}"
        if existing_task.get("effect_key") != effect_key:
            raise AcceptanceExecutorError("ACCEPTANCE-PROMOTION-CONFLICT", "completed task effect key differs from the attempt")
        return {
            "report": report,
            "report_sha256": report_hash,
            "changeset": changeset,
            "changeset_sha256": changeset_hash,
            "effect_key": effect_key,
            "task": existing_task,
            "promoted": True,
        }

    project = work_root.resolve() / "projects" / attempt.project_id.split("/", 1)[1]
    before_project = _capture_tree(project)
    _persist_tree(attempt.root / "transaction-before", before_project)
    preview_changeset = inspect_attempt(
        attempt,
        protocol_root=protocol_root,
        work_root=work_root,
        output_root=output_root,
    )
    _write_transaction_journal(
        attempt,
        protocol_root=protocol_root,
        evaluated_at=evaluated_at,
        report=report,
        report_hash=report_hash,
        stage="PREPARED",
        changeset_hash=canonical_sha256(preview_changeset),
    )
    promoted = False
    try:
        changeset = promote_attempt(attempt, protocol_root=protocol_root, work_root=work_root, output_root=output_root)
        promoted = True
        changeset_hash = canonical_sha256(changeset)
        _write_transaction_journal(
            attempt,
            protocol_root=protocol_root,
            evaluated_at=evaluated_at,
            report=report,
            report_hash=report_hash,
            stage="PROMOTED",
            changeset_hash=changeset_hash,
        )
        findings = validate_repository(work_root.resolve(), attempt.project_id, protocol_root=protocol_root.resolve(), work_root=work_root.resolve())
        if findings:
            raise AcceptanceExecutorError("ACCEPTANCE-GATE-FAILED", "promoted project failed blocking validation")
        effect_key = f"acceptance/{report_hash}/{changeset_hash}"
        task_result = task_runtime.complete(
            work_root.resolve(),
            attempt.project_id,
            attempt.task_id,
            worker_id,
            lease_token,
            {"report_id": report["report_id"], "report_sha256": report_hash, "changeset_sha256": changeset_hash},
            effect_key=effect_key,
            now=evaluated_at,
        )
        _write_transaction_journal(
            attempt,
            protocol_root=protocol_root,
            evaluated_at=evaluated_at,
            report=report,
            report_hash=report_hash,
            stage="COMMITTED",
            changeset_hash=changeset_hash,
        )
        return {
            "report": report,
            "report_sha256": report_hash,
            "changeset": changeset,
            "changeset_sha256": changeset_hash,
            "effect_key": effect_key,
            "task": task_result,
            "promoted": True,
        }
    except Exception as exc:
        if promoted:
            try:
                _restore_tree(project, _load_persisted_tree(attempt.root / "transaction-before"))
                _write_transaction_journal(
                    attempt,
                    protocol_root=protocol_root,
                    evaluated_at=evaluated_at,
                    report=report,
                    report_hash=report_hash,
                    stage="ROLLED_BACK",
                    changeset_hash=canonical_sha256(changeset) if "changeset" in locals() else None,
                )
            except Exception as rollback_exc:
                raise AcceptanceExecutorError("ACCEPTANCE-ROLLBACK", "transaction rollback failed") from rollback_exc
        if isinstance(exc, AcceptanceExecutorError):
            raise
        if isinstance(exc, AttemptWorkspaceError):
            raise AcceptanceExecutorError("ACCEPTANCE-PROMOTION-CONFLICT", "attempt promotion could not complete") from exc
        raise AcceptanceExecutorError("ACCEPTANCE-ROLLBACK", "acceptance transaction failed") from exc


__all__ = [
    "AcceptanceExecutorError",
    "complete_attempt",
    "execute_acceptance",
    "load_acceptance_checks",
    "recover_attempt_transaction",
    "run_acceptance",
]
