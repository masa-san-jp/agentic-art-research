#!/usr/bin/env python3
"""Run deterministic end-to-end quality and safety evaluations offline."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json, yaml_list
from audit import audit_graph
from build_graph import build_graph
from impact import impact_report
from new_project import create_project
from private_evidence_adapter import AdapterError as PrivateEvidenceAdapterError, adapt_private_evidence
from run_project import run_offline_fixture
from state_machine import replay_project
from task_runtime import claim_next, complete, initialize_runtime, load_runtime, resume
from validate import validate_repository


EVALUATION_SCHEMA = "urn:agentic-art-research:evaluation:v1"
TERMINAL_STATUSES = {"COMPLETE", "COMPLETE_WITH_GAPS"}
EXPECTED_TRACE_IDS = {"CL001", "CL002", "IN001", "DC001", "RQ001", "AT001"}


class EvaluationError(ValueError):
    """Raised when an evaluation cannot be run against the requested fixture."""


def _check(name: str, passed: bool, details: list[str]) -> dict[str, Any]:
    return {"id": name, "passed": passed, "details": details}


def _private_probe() -> tuple[bool, list[str]]:
    """Exercise the private boundary with synthetic metadata only."""
    record: dict[str, Any] = {
        "id": "EV990",
        "source_type": "approved-personal-derived",
        "source_location": "gdrive://opaque-eval-990",
        "created_at": "unknown",
        "acquired_at": "2026-08-11T00:00:00+00:00",
        "content_hash": "sha256:" + "0" * 64,
        "rights_status": "private-use-only",
        "sensitivity": "PRIVATE_DERIVED",
        "redistribution": "prohibited",
        "related_projects": ["project/eval-probe"],
        "related_questions": ["Q001"],
        "extraction_status": "processed",
        "direct_observation": False,
        "approval": {
            "purpose": "artistic-research",
            "operation": "export-signals",
            "approval_ref": "consent://opaque-eval-990",
            "approved_at": "2026-08-11T00:00:00+00:00",
        },
        "signal_source": {
            "schema": "urn:synthetic-derived-signal:v1",
            "source_repository": "fixture/evaluation",
            "source_commit": "b" * 40,
        },
        "subject": "subject/eval-fixture",
        "approved_derived_signals": [
            {
                "signal_id": "SIG990",
                "category": "drawn_toward",
                "code": "reversible-process",
                "certainty": "unknown",
                "evidence_refs": ["EV990"],
            }
        ],
    }
    details: list[str] = []
    try:
        output = adapt_private_evidence(record)
        safe_keys = {"schema", "subject", "evidence", "approval", "signal_source", "approved_derived_signals"}
        if set(output) != safe_keys or output["evidence"]["sensitivity"] != "PRIVATE_DERIVED":
            return False, ["safe descriptor did not normalize to the expected repository boundary"]
        details.append("approved derived descriptor accepted")
    except PrivateEvidenceAdapterError as exc:
        return False, [f"approved derived descriptor rejected: {exc}"]

    for field, value in (("sensitivity", "PRIVATE_RAW"), ("sensitivity", "RESTRICTED"), ("raw_text", "synthetic")):
        rejected = dict(record)
        rejected[field] = value
        try:
            adapt_private_evidence(rejected)
        except PrivateEvidenceAdapterError:
            details.append(f"unsafe {field} input rejected")
        else:
            return False, [*details, f"unsafe {field} input was accepted"]
    return True, details


def _resume_probe() -> tuple[bool, list[str]]:
    with tempfile.TemporaryDirectory(prefix="agentic-art-eval-") as temporary:
        probe_root = Path(temporary)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(ROOT / name, probe_root / name)
        (probe_root / "projects").mkdir()
        (probe_root / "data").mkdir()
        create_project(probe_root, "eval-probe", "Evaluation Probe", created_at="2026-08-11T00:00:00+00:00")
        initialize_runtime(
            probe_root,
            "project/eval-probe",
            [{"id": "TASK001", "depends_on": [], "max_attempts": 2}],
            initialized_at="2026-08-11T00:00:00+00:00",
        )
        first = claim_next(
            probe_root,
            "project/eval-probe",
            "eval-worker-a",
            now="2026-08-11T00:00:00+00:00",
            lease_seconds=5,
        )
        if not first:
            return False, ["resume probe could not claim its task"]
        recovered = resume(probe_root, "project/eval-probe", now="2026-08-11T00:00:06+00:00")
        second = claim_next(probe_root, "project/eval-probe", "eval-worker-b", now="2026-08-11T00:00:06+00:00")
        if not second:
            return False, ["resume probe could not reclaim its expired task"]
        complete(
            probe_root,
            "project/eval-probe",
            "TASK001",
            "eval-worker-b",
            second["lease_token"],
            {"value": "synthetic"},
            now="2026-08-11T00:00:07+00:00",
        )
        repeated = complete(
            probe_root,
            "project/eval-probe",
            "TASK001",
            "eval-worker-b",
            second["lease_token"],
            {"value": "duplicate-is-ignored"},
            now="2026-08-11T00:00:08+00:00",
        )
        runtime = load_runtime(probe_root, "project/eval-probe")
        events = read_jsonl(probe_root / "projects/eval-probe/07_runtime/run-log.jsonl")
        succeeded = [event for event in events if event.get("event_type") == "TASK_SUCCEEDED"]
        passed = (
            recovered["recovered"] == ["TASK001"]
            and runtime["tasks"]["TASK001"]["status"] == "SUCCEEDED"
            and repeated["effect_key"] == "TASK001"
            and len(succeeded) == 1
            and len({event["event_id"] for event in events}) == len(events)
        )
        return passed, [
            "expired lease recovered",
            "task completed after reclaim",
            "duplicate completion preserved one effect",
        ] if passed else ["resume probe did not preserve a single completed effect"]


def _ensure_workspace(root: Path) -> None:
    """Create only missing evaluation workspace directories and templates."""
    root.mkdir(parents=True, exist_ok=True)
    for name in ("templates", "config", "schemas"):
        destination = root / name
        if not destination.exists():
            shutil.copytree(ROOT / name, destination)
        elif not destination.is_dir():
            raise EvaluationError(f"evaluation workspace entry is not a directory: {destination}")
    (root / "projects").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)


def evaluate_offline_fixture(root: Path, fixture: Path, *, slug: str = "harmony-study") -> dict[str, Any]:
    """Materialize one synthetic project and evaluate all MVP quality gates."""
    root = root.resolve()
    fixture = fixture.resolve()
    _ensure_workspace(root)
    if (root / "projects" / slug).exists():
        raise EvaluationError(f"evaluation target already exists: project/{slug}")
    try:
        project = run_offline_fixture(root, slug, fixture)
    except (FileNotFoundError, ValueError, FileExistsError) as exc:
        raise EvaluationError(str(exc)) from exc

    findings = validate_repository(root)
    report = load_json(project / "07_runtime" / "completion-report.json")
    state = load_json(project / "07_runtime" / "research-state.json")
    manifest = load_yaml(project / "manifest.yaml") or {}
    graph = build_graph(root)
    impact = impact_report(graph, "EV001")
    replay = replay_project(root, f"project/{slug}")
    audit_findings = audit_graph(graph, root=root, now=datetime(2026, 8, 11, tzinfo=timezone.utc))
    questions = yaml_list(project / "01_planning" / "question-register.yaml", "questions")

    downstream = {item["id"] for item in impact["downstream"]}
    upstream = {item["id"] for item in impact["upstream"]}
    observed_status = report.get("status")
    accuracy = _check(
        "accuracy",
        observed_status == "COMPLETE_WITH_GAPS" and EXPECTED_TRACE_IDS.issubset(downstream),
        [
            f"fixture status={observed_status}",
            f"trace ids missing={sorted(EXPECTED_TRACE_IDS - downstream)}",
        ],
    )
    traceability = _check(
        "traceability",
        impact.get("found") is True and upstream == {"Q001"} and EXPECTED_TRACE_IDS.issubset(downstream),
        [f"upstream={sorted(upstream)}", f"downstream_count={len(downstream)}"],
    )
    termination = _check(
        "termination",
        observed_status in TERMINAL_STATUSES
        and replay.final_status == observed_status
        and state.get("current_task") is None
        and all(
            question.get("priority") != "mandatory" or question.get("status") in {"ANSWERED", "UNRESOLVED", "BLOCKED", "CANCELLED"}
            for question in questions
        ),
        [f"replayed_status={replay.final_status}", f"event_count={replay.event_count}"],
    )
    resumed, resume_details = _resume_probe()
    resume_check = _check("resume", resumed, resume_details)
    private_safe, private_details = _private_probe()
    privacy = _check(
        "privacy",
        not findings and report.get("checks", {}).get("no_private_raw_in_git") is True and private_safe,
        [
            f"repository_findings={len(findings)}",
            *private_details,
        ],
    )
    audit = _check("audit", not audit_findings, [f"audit_findings={len(audit_findings)}"])
    checks = [accuracy, traceability, termination, resume_check, privacy, audit]
    return {
        "schema": EVALUATION_SCHEMA,
        "fixture": fixture.name,
        "project_id": f"project/{slug}",
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic offline quality and safety evaluations.")
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--slug", default="harmony-study")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        content = stable_json(evaluate_offline_fixture(args.root, args.offline_fixture, slug=args.slug))
    except (EvaluationError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
