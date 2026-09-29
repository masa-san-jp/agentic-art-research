"""Build and verify the short-lived feedback passed from one retry to the next."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import load_json, stable_json
from canonical import canonical_sha256
import task_runtime


class RetryFeedbackError(ValueError):
    """A prior acceptance report cannot be used as retry context."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


def _validator(protocol_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    registry = Registry().with_resources([
        (common["$id"], Resource.from_contents(common)),
        (schema["$id"], Resource.from_contents(schema)),
    ])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate(protocol_root: Path, name: str, value: dict[str, Any]) -> None:
    errors = sorted(_validator(protocol_root, name).iter_errors(value), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.absolute_path) or "$"
        raise RetryFeedbackError("RETRY-FEEDBACK-SCHEMA", f"{name}#{field}: {error.message}")


def _attempt_number(value: str) -> int:
    match = re.fullmatch(r"AT([0-9]+)", value)
    if not match:
        raise RetryFeedbackError("RETRY-FEEDBACK-ATTEMPT", "attempt IDs must be numeric for retry ordering")
    return int(match.group(1))


def _prior_report_path(work_root: Path, run_id: str, task_id: str, current_attempt_id: str) -> tuple[str, Path] | None:
    current_number = _attempt_number(current_attempt_id)
    if current_number <= 1:
        return None
    prior_id = f"AT{current_number - 1:03d}"
    report = work_root.resolve() / ".harness" / "attempts" / run_id / task_id / prior_id / "acceptance-report.json"
    return prior_id, report


def _finding_rows(report: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    for gate in report.get("gates", []):
        if not isinstance(gate, dict) or gate.get("status") != "FAIL":
            continue
        raw_findings = gate.get("findings")
        findings = raw_findings if isinstance(raw_findings, list) and raw_findings else [
            str(gate.get("rule") or "Acceptance gate failed.")
        ]
        for finding in findings:
            if not isinstance(finding, str) or not finding.strip():
                raise RetryFeedbackError("RETRY-FEEDBACK-REPORT", "failed gate finding must be a non-empty string")
            rows.append({
                "gate_id": gate.get("id"),
                "kind": gate.get("kind"),
                "path": gate.get("path"),
                "rule": gate.get("rule"),
                "remediation": gate.get("remediation"),
                "finding": finding[:500],
            })
    rows.sort(key=lambda row: (
        str(row.get("gate_id") or ""),
        str(row.get("path") or ""),
        str(row.get("finding") or ""),
    ))
    return rows[:20], max(0, len(rows) - 20)


def build_retry_feedback(
    *,
    protocol_root: Path,
    work_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    current_attempt_id: str,
) -> dict[str, Any] | None:
    """Return only the immediately preceding failed acceptance report."""

    prior = _prior_report_path(work_root, run_id, task_id, current_attempt_id)
    if prior is None:
        return None
    source_attempt_id, report_path = prior
    if not report_path.is_file():
        return None
    report = load_json(report_path)
    if not isinstance(report, dict):
        raise RetryFeedbackError("RETRY-FEEDBACK-REPORT", "acceptance report must be an object")
    _validate(protocol_root.resolve(), "acceptance-report", report)
    if any(report.get(key) != expected for key, expected in {
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": source_attempt_id,
    }.items()):
        raise RetryFeedbackError("RETRY-FEEDBACK-PROVENANCE", "acceptance report identity does not match the retry")
    if report.get("status") != "FAIL":
        return None
    report_hash = canonical_sha256(report)
    try:
        runtime = task_runtime.load_runtime(work_root.resolve(), project_id)
        task = (runtime.get("tasks") or {}).get(task_id)
        failure = task.get("last_failure", task.get("failure")) if isinstance(task, dict) else None
    except Exception as exc:
        raise RetryFeedbackError("RETRY-FEEDBACK-PROVENANCE", "task runtime failure provenance is unavailable") from exc
    if not isinstance(failure, dict) or any(
        failure.get(key) != expected
        for key, expected in {
            "attempt": _attempt_number(source_attempt_id),
            "report_id": report.get("report_id"),
            "report_sha256": report_hash,
        }.items()
    ):
        raise RetryFeedbackError("RETRY-FEEDBACK-PROVENANCE", "acceptance report hash is not recorded for the failed attempt")
    findings, findings_omitted = _finding_rows(report)
    if not findings:
        raise RetryFeedbackError("RETRY-FEEDBACK-REPORT", "failed acceptance report has no failed gates")
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "source_attempt_id": source_attempt_id,
        "report_id": report["report_id"],
        "report_sha256": report_hash,
        "findings": findings,
        "findings_omitted": findings_omitted,
    }
    payload["feedback_id"] = "RF-" + hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()[:16]
    _validate(protocol_root.resolve(), "retry-feedback", {**payload})
    return payload


__all__ = ["RetryFeedbackError", "build_retry_feedback"]
