from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json, yaml_list
from state_machine import TransitionError, load_state_machine
from validate import validate_repository


CHECK_NAMES = (
    "manifest_valid",
    "mandatory_questions_terminal",
    "evidence_ledger_valid",
    "claim_references_resolved",
    "decisions_traceable",
    "requirements_testable",
    "rights_review_complete",
    "privacy_review_complete",
    "no_private_raw_in_git",
    "completion_report_terminal",
)
CORE_CHECKS = {
    "manifest_valid",
    "mandatory_questions_terminal",
    "evidence_ledger_valid",
    "claim_references_resolved",
    "decisions_traceable",
    "requirements_testable",
    "no_private_raw_in_git",
    "completion_report_terminal",
}
TERMINAL_STATUSES = {"COMPLETE", "COMPLETE_WITH_GAPS"}


def _project_path(root: Path, target: str) -> Path:
    if not target.startswith("project/") or target.count("/") != 1:
        raise ValueError("target must be project/<slug>")
    projects_root = (root / "projects").resolve()
    project = (projects_root / target.split("/", 1)[1]).resolve()
    if project.parent != projects_root or not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


def _project_findings(findings: list[Any], project: Path, root: Path) -> list[Any]:
    prefix = f"{project.relative_to(root)}/"
    return [finding for finding in findings if finding.path.startswith(prefix)]


def _has_rule(findings: list[Any], *rules: str) -> bool:
    return any(any(finding.rule == rule or finding.rule.startswith(f"{rule}:") for rule in rules) for finding in findings)


def _review_complete(path: Path, key: str) -> bool:
    try:
        value = load_yaml(path) or {}
    except Exception:
        return False
    if key == "rights":
        records = value.get("rights") if isinstance(value, dict) else None
        return isinstance(records, list) and bool(records) and all(
            isinstance(record, dict) and record.get("status") in {"CLEARED", "APPROVED", "ALLOWED"}
            for record in records
        )
    if key == "privacy":
        return (
            isinstance(value, dict)
            and value.get("status") in {"COMPLETE", "REVIEWED", "APPROVED"}
            and isinstance(value.get("reviewed_at"), str)
            and value.get("private_raw_in_git") is False
        )
    return False


def evaluate_project(root: Path, target: str) -> dict[str, Any]:
    root = root.resolve()
    project = _project_path(root, target)
    findings = _project_findings(validate_repository(root), project, root)
    manifest_path = project / "manifest.yaml"
    manifest = load_yaml(manifest_path) or {}
    project_data = manifest.get("project") if isinstance(manifest, dict) else {}
    state = load_json(project / "07_runtime" / "research-state.json")
    completion_report = load_json(project / "07_runtime" / "completion-report.json")
    vocab = load_yaml(root / "config" / "vocabularies.yaml") or {}
    terminal_questions = set(vocab.get("question_terminal_statuses", []))
    questions = yaml_list(project / "01_planning" / "question-register.yaml", "questions")
    evidence = read_jsonl(project / "02_evidence" / "evidence-ledger.jsonl")
    claims = read_jsonl(project / "03_knowledge" / "claims.jsonl")
    decisions = yaml_list(project / "04_decisions" / "decision-log.yaml", "decisions")
    requirements = yaml_list(project / "05_production" / "production-requirements.yaml", "requirements")
    acceptance_tests = yaml_list(project / "05_production" / "acceptance-tests.yaml", "acceptance_tests")
    tests_by_id = {record.get("id"): record for record in acceptance_tests}

    checks = {
        "manifest_valid": isinstance(project_data, dict)
        and not _has_rule(findings, "SCHEMA", "PROJECT-ID", "PROJECT-STATUS"),
        "mandatory_questions_terminal": all(
            record.get("priority") != "mandatory" or record.get("status") in terminal_questions for record in questions
        ),
        "evidence_ledger_valid": not _has_rule(findings, "JSONL", "SCHEMA")
        or not any("evidence-ledger.jsonl" in finding.path for finding in findings),
        "claim_references_resolved": not _has_rule(findings, "CROSS-REFERENCE", "DEPENDENCY-CYCLE")
        and not any("claims.jsonl" in finding.path and finding.rule in {"JSONL", "SCHEMA:required"} for finding in findings),
        "decisions_traceable": all(record.get("insight_ids") or record.get("evidence_ids") for record in decisions)
        and not any("decision-log.yaml" in finding.path and _has_rule([finding], "CROSS-REFERENCE", "SCHEMA") for finding in findings),
        "requirements_testable": all(
            requirement.get("priority") != "mandatory"
            or all(
                test_id in tests_by_id and tests_by_id[test_id].get("result") == "PASS"
                for test_id in requirement.get("acceptance_test_ids", [])
            )
            for requirement in requirements
        ),
        "rights_review_complete": _review_complete(project / "06_governance" / "rights-register.yaml", "rights"),
        "privacy_review_complete": _review_complete(project / "06_governance" / "privacy-review.yaml", "privacy"),
        "no_private_raw_in_git": not _has_rule(findings, "DATA-BOUNDARY", "SECRET-SCAN"),
        "completion_report_terminal": isinstance(completion_report, dict)
        and completion_report.get("status") in set(vocab.get("terminal_statuses", [])),
    }
    core_ok = all(checks[name] for name in CORE_CHECKS)
    status = "COMPLETE" if core_ok and all(checks.values()) else "COMPLETE_WITH_GAPS" if core_ok else "BLOCKED"
    gaps = [
        {
            "id": f"GAP-{name.upper()}",
            "reason": f"{name} is not complete.",
            "impact": "Resolve this check before declaring the project fully complete.",
        }
        for name, passed in checks.items()
        if not passed and status == "COMPLETE_WITH_GAPS"
    ]
    blockers = [
        {
            "id": f"BLOCKER-{name.upper()}",
            "reason": f"{name} is not satisfied.",
            "release_condition": "Correct the canonical project data and rerun completion.",
        }
        for name, passed in checks.items()
        if not passed and name in CORE_CHECKS and status == "BLOCKED"
    ]
    existing_completed_at = completion_report.get("completed_at") if isinstance(completion_report, dict) else None
    completed_at = _timestamp(existing_completed_at) or _timestamp(project_data.get("updated_at"))
    if completed_at is None:
        raise ValueError("project has no deterministic RFC 3339 completion timestamp")
    return {
        "project_id": target,
        "status": status,
        "completed_at": completed_at,
        "checks": checks,
        "gaps": gaps,
        "blockers": blockers,
        "reopen_triggers": ["new_evidence", "material_change", "rights_or_privacy_change"],
    }


def complete_project(root: Path, target: str, *, completed_at: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    project = _project_path(root, target)
    manifest_path = project / "manifest.yaml"
    state_path = project / "07_runtime" / "research-state.json"
    report_path = project / "07_runtime" / "completion-report.json"
    run_log_path = project / "07_runtime" / "run-log.jsonl"
    manifest = load_yaml(manifest_path) or {}
    state = load_json(state_path)
    current_status = (manifest.get("project") or {}).get("status")
    if current_status != state.get("status"):
        raise ValueError("manifest and research-state statuses must match before completion")
    if current_status not in {"VALIDATING", *TERMINAL_STATUSES}:
        raise ValueError("completion requires project status VALIDATING or an idempotent terminal status")
    report = evaluate_project(root, target)
    if completed_at is not None:
        normalized = _timestamp(completed_at)
        if normalized is None:
            raise ValueError("completed_at must be an RFC 3339 timestamp")
        report["completed_at"] = normalized
    if current_status in TERMINAL_STATUSES and current_status == report["status"]:
        return report

    before = {path: path.read_text(encoding="utf-8") for path in (manifest_path, state_path, report_path, run_log_path)}
    try:
        manifest["project"]["status"] = report["status"]
        manifest["project"]["updated_at"] = report["completed_at"]
        state["status"] = report["status"]
        state["updated_at"] = report["completed_at"]
        state["last_event_id"] = "COMPLETION-001"
        event = {
            "event_id": "COMPLETION-001",
            "event_type": "STATE_TRANSITION",
            "from_status": current_status,
            "to_status": report["status"],
        }
        try:
            load_state_machine(root).apply(current_status, event)
        except TransitionError as exc:
            raise ValueError(str(exc)) from exc
        atomic_write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False))
        atomic_write_text(state_path, stable_json(state))
        atomic_write_text(report_path, stable_json(report))
        existing_log = run_log_path.read_text(encoding="utf-8")
        atomic_write_text(run_log_path, existing_log + json.dumps(event, ensure_ascii=False) + "\n")
        findings = validate_repository(root)
        if findings:
            raise ValueError("completion produced invalid project:\n" + "\n".join(finding.render() for finding in findings))
    except Exception:
        for path, content in before.items():
            atomic_write_text(path, content)
        raise
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate and write a reproducible terminal completion report.")
    parser.add_argument("target")
    parser.add_argument("--completed-at")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        report = complete_project(args.root.resolve(), args.target, completed_at=args.completed_at)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(stable_json(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
