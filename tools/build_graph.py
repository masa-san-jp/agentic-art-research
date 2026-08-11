from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, iter_project_dirs, load_yaml, read_jsonl, stable_json, yaml_list


def node_key(project_id: str, identifier: str) -> str:
    """Return a stable graph identity for a project-local canonical ID."""
    return f"{project_id}::{identifier}"


def _add_nodes(nodes: dict[str, dict[str, Any]], records: list[dict[str, Any]], kind: str, project_id: str) -> None:
    for record in records:
        identifier = record.get("id")
        if identifier:
            key = node_key(project_id, identifier)
            if key in nodes:
                raise ValueError(f"duplicate graph node: {key}")
            nodes[key] = {"key": key, "id": identifier, "kind": kind, "project_id": project_id}


def _add_edge(
    edges: set[tuple[str, str, str]],
    nodes: dict[str, dict[str, Any]],
    project_id: str,
    source: str,
    target: str,
    relation: str,
) -> None:
    source_key = node_key(project_id, source)
    target_key = node_key(project_id, target)
    if source_key not in nodes or target_key not in nodes:
        missing = source_key if source_key not in nodes else target_key
        raise ValueError(f"unresolved graph reference: {missing}")
    edges.add((source_key, target_key, relation))


def build_graph(root: Path) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str, str]] = set()
    project_index: list[dict[str, Any]] = []
    for project in iter_project_dirs(root):
        project_id = f"project/{project.name}"
        project_index.append({"id": project_id, "path": str(project.relative_to(root))})
        questions = yaml_list(project / "01_planning" / "question-register.yaml", "questions")
        evidence = read_jsonl(project / "02_evidence" / "evidence-ledger.jsonl")
        claims = read_jsonl(project / "03_knowledge" / "claims.jsonl")
        insights = yaml_list(project / "04_decisions" / "insight-register.yaml", "insights")
        decisions = yaml_list(project / "04_decisions" / "decision-log.yaml", "decisions")
        requirements = yaml_list(project / "05_production" / "production-requirements.yaml", "requirements")
        tests = yaml_list(project / "05_production" / "acceptance-tests.yaml", "acceptance_tests")
        hypotheses = yaml_list(project / "04_decisions" / "production-hypotheses.yaml", "hypotheses")
        comparisons = yaml_list(project / "04_decisions" / "hypothesis-comparison.yaml", "comparisons")
        prototype_plans = yaml_list(project / "05_production" / "prototype-plans.yaml", "prototype_plans")
        uncertainties = [
            uncertainty
            for hypothesis in hypotheses
            for uncertainty in hypothesis.get("uncertainties", [])
            if isinstance(uncertainty, dict)
        ]
        prototype_tasks = [
            task
            for plan in prototype_plans
            for task in plan.get("tasks", [])
            if isinstance(task, dict)
        ]
        handoff_path = project / "05_production" / "production-handoff.yaml"
        handoff_value = load_yaml(handoff_path) if handoff_path.exists() else {}
        handoff_value = handoff_value or {}
        handoffs = []
        if isinstance(handoff_value, dict) and handoff_value.get("handoff_id"):
            handoffs = [{"id": handoff_value["handoff_id"], **handoff_value}]

        for records, kind in ((questions, "question"), (evidence, "evidence"), (claims, "claim"), (insights, "insight"), (decisions, "decision"), (requirements, "requirement"), (tests, "acceptance_test")):
            _add_nodes(nodes, records, kind, project_id)
        for records, kind in (
            (hypotheses, "production_hypothesis"),
            (comparisons, "hypothesis_comparison"),
            (uncertainties, "uncertainty"),
            (prototype_plans, "prototype_plan"),
            (prototype_tasks, "prototype_task"),
            (handoffs, "production_handoff"),
        ):
            _add_nodes(nodes, records, kind, project_id)

        for record in evidence:
            for source in record.get("related_questions", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "answered_by")
        for record in claims:
            for source in record.get("evidence_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "supports")
            for source in record.get("supporting_claims", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "supports")
            for source in record.get("opposing_claims", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "opposes")
        for record in insights:
            for source in record.get("claim_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "informs")
            for source in record.get("opposing_claim_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "opposes")
        for record in decisions:
            for source in record.get("insight_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "informs")
            for source in record.get("evidence_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "informs")
        for record in requirements:
            for source in record.get("source_decisions", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "requires")
        for record in tests:
            target = record.get("target_requirement")
            if target:
                _add_edge(edges, nodes, project_id, target, record["id"], "verified_by")
        for record in hypotheses:
            for source in record.get("source_decision_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "informs")
            for source in record.get("source_insight_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "informs")
            for uncertainty in record.get("uncertainties", []):
                if isinstance(uncertainty, dict) and uncertainty.get("id"):
                    _add_edge(edges, nodes, project_id, record["id"], uncertainty["id"], "has_uncertainty")
        for record in comparisons:
            for source in record.get("hypothesis_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "compared_by")
        for record in prototype_plans:
            _add_edge(edges, nodes, project_id, record["hypothesis_id"], record["id"], "prototyped_by")
            for source in record.get("uncertainty_ids", []):
                _add_edge(edges, nodes, project_id, source, record["id"], "tests_uncertainty")
            for source in record.get("acceptance_test_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "evaluated_by")
            for task in record.get("tasks", []):
                if isinstance(task, dict) and task.get("id"):
                    _add_edge(edges, nodes, project_id, record["id"], task["id"], "contains")
                    for dependency in task.get("depends_on", []):
                        _add_edge(edges, nodes, project_id, dependency, task["id"], "precedes")
        for handoff in handoffs:
            handoff_id = handoff["id"]
            selection = handoff.get("selection") if isinstance(handoff.get("selection"), dict) else {}
            selected = selection.get("selected_hypothesis_id")
            candidate_hypothesis_ids = {
                hypothesis_id
                for hypothesis_id in [selected, *selection.get("alternative_hypothesis_ids", [])]
                if isinstance(hypothesis_id, str)
            }
            if selected:
                _add_edge(edges, nodes, project_id, selected, handoff_id, "selected_for")
            for alternative in selection.get("alternative_hypothesis_ids", []):
                _add_edge(edges, nodes, project_id, alternative, handoff_id, "alternative_for")
            for source in handoff.get("prototype_plan_ids", []):
                _add_edge(edges, nodes, project_id, source, handoff_id, "included_prototype")
            for requirement in handoff.get("requirements", []):
                if isinstance(requirement, dict) and requirement.get("id"):
                    _add_edge(edges, nodes, project_id, requirement["id"], handoff_id, "included_requirement")
                    for test_id in requirement.get("acceptance_test_ids", []):
                        _add_edge(edges, nodes, project_id, test_id, handoff_id, "included_test")
            source_refs = handoff.get("source_refs") if isinstance(handoff.get("source_refs"), dict) else {}
            for field in ("decision_ids", "insight_ids", "evidence_ids"):
                for source in source_refs.get(field, []):
                    _add_edge(edges, nodes, project_id, source, handoff_id, "source_ref")
            for comparison in comparisons:
                comparison_hypothesis_ids = {
                    hypothesis_id
                    for hypothesis_id in comparison.get("hypothesis_ids", [])
                    if isinstance(hypothesis_id, str)
                }
                if candidate_hypothesis_ids.intersection(comparison_hypothesis_ids):
                    _add_edge(edges, nodes, project_id, comparison["id"], handoff_id, "comparison_basis")

    return {
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [{"from": source, "to": target, "type": kind} for source, target, kind in sorted(edges)],
        "projects": sorted(project_index, key=lambda item: item["id"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic dependency graph from canonical project data.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Fail if generated output is stale.")
    args = parser.parse_args()
    root = args.root.resolve()
    graph = build_graph(root)
    output = root / "data" / "dependency-graph.json"
    content = stable_json(graph)
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != content:
            print(f"STALE: {output}")
            return 1
        print("OK: generated graph is current")
        return 0
    atomic_write_text(output, content)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
