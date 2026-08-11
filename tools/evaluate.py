from __future__ import annotations

import argparse
import shutil
import tempfile
from collections import deque
from pathlib import Path
from typing import Any

from _common import ROOT, load_json, load_yaml, read_jsonl, stable_json, yaml_list
from build_graph import build_graph
from complete import evaluate_project as evaluate_completion
from private_evidence import PrivateEvidenceAdapter
from run_project import run_offline_fixture
from state_machine import replay_project
from task_runtime import load_runtime
from validate import validate_repository


class EvaluationError(ValueError):
    """Raised when an evaluation request or policy is invalid."""


def _project(root: Path, target: str) -> Path:
    if not isinstance(target, str) or not target.startswith("project/") or target.count("/") != 1:
        raise EvaluationError("target must be project/<slug>")
    projects_root = (root / "projects").resolve()
    project = (projects_root / target.split("/", 1)[1]).resolve()
    if project.parent != projects_root or not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def _policy(root: Path) -> dict[str, Any]:
    value = load_yaml(root / "config" / "evaluation.yaml") or {}
    if not isinstance(value, dict):
        raise EvaluationError("config/evaluation.yaml must be a mapping")
    gates = value.get("required_gates")
    if not isinstance(gates, list) or not gates or any(not isinstance(gate, str) or not gate for gate in gates):
        raise EvaluationError("config/evaluation.yaml: required_gates must be a non-empty string list")
    if len(set(gates)) != len(gates):
        raise EvaluationError("config/evaluation.yaml: required_gates contains duplicates")
    ratio = value.get("minimum_traceability_ratio")
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 <= ratio <= 1:
        raise EvaluationError("config/evaluation.yaml: minimum_traceability_ratio must be between 0 and 1")
    return value


def _gate(passed: bool, score: float, **details: Any) -> dict[str, Any]:
    return {"passed": bool(passed), "score": round(float(score), 6), **details}


def _accuracy_gate(root: Path, target: str, findings: list[Any], expected_status: str | None) -> dict[str, Any]:
    project = _project(root, target)
    manifest = load_yaml(project / "manifest.yaml") or {}
    state = load_json(project / "07_runtime" / "research-state.json")
    report = load_json(project / "07_runtime" / "completion-report.json")
    completion = evaluate_completion(root, target)
    statuses_match = (
        isinstance(manifest, dict)
        and isinstance(state, dict)
        and isinstance(report, dict)
        and (manifest.get("project") or {}).get("status") == state.get("status") == report.get("status")
    )
    expected_match = expected_status is None or report.get("status") == expected_status
    passed = not findings and statuses_match and expected_match and completion.get("status") == report.get("status")
    return _gate(
        passed,
        1.0 if passed else 0.0,
        validation_findings=len(findings),
        status_consistent=statuses_match,
        expected_status=expected_status,
        expected_status_match=expected_match,
    )


def _evidence_ancestors(graph: dict[str, Any], project_id: str, requirement_id: str) -> list[str]:
    nodes = {node["key"]: node for node in graph.get("nodes", [])}
    target = f"{project_id}::{requirement_id}"
    reverse: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        reverse.setdefault(edge["to"], []).append(edge["from"])
    queue = deque([target])
    visited = {target}
    evidence: set[str] = set()
    while queue:
        current = queue.popleft()
        for source in sorted(reverse.get(current, [])):
            if source in visited:
                continue
            visited.add(source)
            node = nodes.get(source)
            if node and node.get("kind") == "evidence":
                evidence.add(node["id"])
            else:
                queue.append(source)
    return sorted(evidence)


def _traceability_gate(root: Path, target: str, graph: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    project = _project(root, target)
    requirements = yaml_list(project / "05_production" / "production-requirements.yaml", "requirements")
    tests = yaml_list(project / "05_production" / "acceptance-tests.yaml", "acceptance_tests")
    tests_by_id = {record.get("id"): record for record in tests}
    mandatory = [record for record in requirements if record.get("priority") == "mandatory"]
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for requirement in mandatory:
        requirement_id = requirement.get("id")
        test_ids = requirement.get("acceptance_test_ids", [])
        testable = isinstance(test_ids, list) and bool(test_ids) and all(
            isinstance(test_id, str)
            and test_id in tests_by_id
            and tests_by_id[test_id].get("target_requirement") == requirement_id
            and tests_by_id[test_id].get("result") == "PASS"
            for test_id in test_ids
        )
        evidence_ids = _evidence_ancestors(graph, target, requirement_id)
        detail = {"id": requirement_id, "evidence_ids": evidence_ids, "testable": testable}
        if testable and evidence_ids:
            resolved.append(detail)
        else:
            missing.append(requirement_id)
    ratio = len(resolved) / len(mandatory) if mandatory else 1.0
    minimum = float(policy["minimum_traceability_ratio"])
    return _gate(
        ratio >= minimum,
        ratio,
        mandatory_requirements=len(mandatory),
        resolved_requirements=resolved,
        missing_requirements=sorted(value for value in missing if isinstance(value, str)),
        minimum_ratio=minimum,
    )


def _termination_gate(root: Path, target: str) -> dict[str, Any]:
    project = _project(root, target)
    manifest = load_yaml(project / "manifest.yaml") or {}
    state = load_json(project / "07_runtime" / "research-state.json")
    report = load_json(project / "07_runtime" / "completion-report.json")
    vocabulary = load_yaml(root / "config" / "vocabularies.yaml") or {}
    terminal_statuses = set(vocabulary.get("terminal_statuses", []))
    question_terminal_statuses = set(vocabulary.get("question_terminal_statuses", []))
    questions = yaml_list(project / "01_planning" / "question-register.yaml", "questions")
    question_ok = all(
        record.get("priority") != "mandatory" or record.get("status") in question_terminal_statuses for record in questions
    )
    project_status = (manifest.get("project") or {}).get("status") if isinstance(manifest, dict) else None
    statuses_ok = project_status in terminal_statuses and state.get("status") == project_status and report.get("status") == project_status
    task_runtime = state.get("task_runtime") if isinstance(state, dict) else None
    task_statuses = set(vocabulary.get("task_statuses", []))
    terminal_task_statuses = task_statuses - {"PENDING", "RUNNING"}
    tasks_ok = True
    task_count = 0
    if isinstance(task_runtime, dict):
        tasks = task_runtime.get("tasks")
        if not isinstance(tasks, dict):
            tasks_ok = False
        else:
            task_count = len(tasks)
            tasks_ok = all(task.get("status") in terminal_task_statuses for task in tasks.values() if isinstance(task, dict))
    passed = statuses_ok and question_ok and tasks_ok
    return _gate(
        passed,
        1.0 if passed else 0.0,
        project_status=project_status,
        project_status_terminal=project_status in terminal_statuses,
        mandatory_questions_terminal=question_ok,
        task_count=task_count,
        task_runtime_terminal=tasks_ok,
    )


def _resume_gate(root: Path, target: str) -> dict[str, Any]:
    project = _project(root, target)
    state = load_json(project / "07_runtime" / "research-state.json")
    events = read_jsonl(project / "07_runtime" / "run-log.jsonl")
    event_ids = {event.get("event_id", event.get("id")) for event in events}
    replay = replay_project(root, target)
    resume_text = state.get("resume_from") if isinstance(state, dict) else None
    last_event_id = state.get("last_event_id") if isinstance(state, dict) else None
    state_event_ok = last_event_id in event_ids if last_event_id is not None else False
    task_runtime = state.get("task_runtime") if isinstance(state, dict) else None
    task_runtime_ok = True
    if isinstance(task_runtime, dict):
        task_runtime_ok = isinstance(load_runtime(root, target), dict)
    passed = (
        replay.final_status == state.get("status")
        and replay.event_count == len(events)
        and isinstance(resume_text, str)
        and bool(resume_text.strip())
        and state_event_ok
        and task_runtime_ok
    )
    return _gate(
        passed,
        1.0 if passed else 0.0,
        replay_event_count=replay.event_count,
        replay_transition_count=replay.transition_count,
        replay_final_status=replay.final_status,
        state_event_present=state_event_ok,
        task_runtime_replayable=task_runtime_ok,
    )


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(key in forbidden or _contains_key(item, forbidden) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _privacy_gate(root: Path, target: str, findings: list[Any]) -> dict[str, Any]:
    project = _project(root, target)
    vocabulary = load_yaml(root / "config" / "vocabularies.yaml") or {}
    prohibited_classes = {"PRIVATE_RAW", "RESTRICTED"}
    evidence = read_jsonl(project / "02_evidence" / "evidence-ledger.jsonl")
    prohibited_records = [record.get("id") for record in evidence if record.get("sensitivity") in prohibited_classes]
    boundary_findings = [finding.render() for finding in findings if finding.rule in {"DATA-BOUNDARY", "SECRET-SCAN"}]
    adapter = PrivateEvidenceAdapter(root)
    metadata = {
        "source_type": "private-source-metadata",
        "source_location": "gdrive://synthetic-eval-source",
        "content_hash": "sha256:" + "b" * 64,
        "rights_status": "private-use-only",
        "related_projects": [target],
        "related_questions": ["Q001"],
        "acquired_at": "2026-08-11T00:00:00+09:00",
    }
    signal = {
        "statement": "Synthetic preference signal for evaluation.",
        "scope": "creator/synthetic",
        "epistemic_status": "SUPPORTED",
        "valid_from": "2026-08-11",
        "review_after": "2027-08-11",
    }
    derived_records = adapter.build_records(metadata, signal, evidence_id="EV999", claim_id="CL999")
    forbidden_fields = set(adapter.forbidden_input_fields)
    output_contains_forbidden = _contains_key(derived_records, forbidden_fields)
    sensitivity_ok = derived_records["evidence"].get("sensitivity") not in prohibited_classes
    vocabulary_ok = derived_records["claim"].get("type") in set(vocabulary.get("claim_types", []))
    passed = not prohibited_records and not boundary_findings and not output_contains_forbidden and sensitivity_ok and vocabulary_ok
    return _gate(
        passed,
        1.0 if passed else 0.0,
        prohibited_records=sorted(value for value in prohibited_records if isinstance(value, str)),
        boundary_findings=boundary_findings,
        adapter_output_contains_forbidden_fields=output_contains_forbidden,
        adapter_sensitivity_allowed=sensitivity_ok,
        adapter_claim_type_configured=vocabulary_ok,
    )


def evaluate_project(root: Path, target: str, *, expected_status: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    policy = _policy(root)
    findings = validate_repository(root)
    gates: dict[str, dict[str, Any]] = {}
    try:
        gates["accuracy"] = _accuracy_gate(root, target, findings, expected_status)
    except (OSError, ValueError, TypeError) as exc:
        gates["accuracy"] = _gate(False, 0.0, error=str(exc), validation_findings=len(findings))
    try:
        graph = build_graph(root)
        gates["traceability"] = _traceability_gate(root, target, graph, policy)
    except (OSError, ValueError, TypeError) as exc:
        gates["traceability"] = _gate(False, 0.0, error=str(exc))
    try:
        gates["termination"] = _termination_gate(root, target)
    except (OSError, ValueError, TypeError) as exc:
        gates["termination"] = _gate(False, 0.0, error=str(exc))
    try:
        gates["resume"] = _resume_gate(root, target)
    except (OSError, ValueError, TypeError) as exc:
        gates["resume"] = _gate(False, 0.0, error=str(exc))
    try:
        gates["privacy"] = _privacy_gate(root, target, findings)
    except (OSError, ValueError, TypeError) as exc:
        gates["privacy"] = _gate(False, 0.0, error=str(exc))
    required = policy["required_gates"]
    passed = all(gates.get(name, {}).get("passed") is True for name in required)
    return {"version": 1, "project_id": target, "required_gates": required, "gates": gates, "passed": passed}


def evaluate_offline_fixture(root: Path, fixture: Path, slug: str = "harmony-study") -> dict[str, Any]:
    root = root.resolve()
    fixture = fixture.resolve()
    if not fixture.is_dir():
        raise FileNotFoundError(f"offline fixture not found: {fixture}")
    with tempfile.TemporaryDirectory(prefix="agentic-art-eval-") as temporary:
        scratch = Path(temporary)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(root / name, scratch / name)
        (scratch / "projects").mkdir()
        (scratch / "data").mkdir()
        run_offline_fixture(scratch, slug, fixture)
        expected_status = _policy(scratch).get("expected_offline_fixture_status")
        if not isinstance(expected_status, str) or not expected_status:
            raise EvaluationError("config/evaluation.yaml: expected_offline_fixture_status must be a non-empty string")
        result = evaluate_project(scratch, f"project/{slug}", expected_status=expected_status)
    result["fixture"] = fixture.name
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic quality and safety gates for a research project.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--target")
    parser.add_argument("--offline-fixture", type=Path)
    parser.add_argument("--slug", default="harmony-study")
    args = parser.parse_args()
    if bool(args.target) == bool(args.offline_fixture):
        parser.error("provide exactly one of --target or --offline-fixture")
    try:
        result = (
            evaluate_offline_fixture(args.root, args.offline_fixture, args.slug)
            if args.offline_fixture
            else evaluate_project(args.root, args.target)
        )
    except (EvaluationError, FileNotFoundError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
