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


def _add_qualified_edge(
    edges: set[tuple[str, str, str]],
    nodes: dict[str, dict[str, Any]],
    source: str,
    target: str,
    relation: str,
) -> None:
    """Add an edge whose endpoints are already project/profile-qualified keys."""
    if source not in nodes or target not in nodes:
        missing = source if source not in nodes else target
        raise ValueError(f"unresolved graph reference: {missing}")
    edges.add((source, target, relation))


def _profile_signal_records(profiles_root: Path | None) -> list[dict[str, Any]]:
    if profiles_root is None:
        return []
    if profiles_root.is_file():
        paths = [profiles_root] if profiles_root.name == "aesthetic-signals.yaml" else []
    elif profiles_root.is_dir():
        paths = sorted(profiles_root.rglob("aesthetic-signals.yaml"))
    else:
        paths = []
    records: list[dict[str, Any]] = []
    for path in paths:
        value = load_yaml(path) or {}
        if isinstance(value, dict) and isinstance(value.get("signals"), list):
            records.extend(signal for signal in value["signals"] if isinstance(signal, dict))
    return records


def build_graph(
    root: Path,
    profiles_root: Path | None = None,
    *,
    protocol_root: Path | None = None,
    work_root: Path | None = None,
) -> dict[str, Any]:
    # Graph inputs and generated output belong to work_root.  protocol_root is
    # accepted as an explicit context parameter for callers that share one
    # root contract across tools; this graph currently has no protocol reads.
    del protocol_root
    root = (work_root or root).resolve()
    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str, str]] = set()
    project_index: list[dict[str, Any]] = []
    for project in iter_project_dirs(root):
        project_id = f"project/{project.name}"
        project_index.append({"id": project_id, "path": str(project.relative_to(root))})
        questions = yaml_list(project / "01_planning" / "question-register.yaml", "questions")
        evidence = read_jsonl(project / "02_evidence" / "evidence-ledger.jsonl")
        claims = read_jsonl(project / "03_knowledge" / "claims.jsonl")
        knowledge_observations = read_jsonl(project / "03_knowledge" / "observations.jsonl")
        relationships = read_jsonl(project / "03_knowledge" / "relationships.jsonl")
        contradictions = read_jsonl(project / "03_knowledge" / "contradictions.jsonl")
        external_references = read_jsonl(project / "03_knowledge" / "external-references.jsonl")
        insights = yaml_list(project / "04_decisions" / "insight-register.yaml", "insights")
        decisions = yaml_list(project / "04_decisions" / "decision-log.yaml", "decisions")
        rejected_options = yaml_list(project / "04_decisions" / "rejected-options.yaml", "rejected_options")
        registry_uncertainties = yaml_list(project / "04_decisions" / "uncertainty-register.yaml", "uncertainties")
        requirements = yaml_list(project / "05_production" / "production-requirements.yaml", "requirements")
        tests = yaml_list(project / "05_production" / "acceptance-tests.yaml", "acceptance_tests")
        hypotheses = yaml_list(project / "04_decisions" / "production-hypotheses.yaml", "hypotheses")
        comparisons = yaml_list(project / "04_decisions" / "hypothesis-comparison.yaml", "comparisons")
        prototype_plans = yaml_list(project / "05_production" / "prototype-plans.yaml", "prototype_plans")
        production_results = read_jsonl(project / "07_runtime" / "production-feedback-imports.jsonl")
        production_observations: list[dict[str, Any]] = []
        for result in production_results:
            if not isinstance(result, dict) or not isinstance(result.get("result_id"), str):
                continue
            result_observations = result.get("observations", [])
            if not isinstance(result_observations, list):
                continue
            for observation in result_observations:
                if not isinstance(observation, dict) or not isinstance(observation.get("id"), str):
                    continue
                production_observations.append(
                    {
                        **observation,
                        "id": f"{result['result_id']}/{observation['id']}",
                        "result_id": result["result_id"],
                    }
                )
        inline_uncertainties = [
            uncertainty
            for hypothesis in hypotheses
            for uncertainty in hypothesis.get("uncertainties", [])
            if isinstance(uncertainty, dict)
        ]
        registered_uncertainty_ids = {
            uncertainty.get("id")
            for uncertainty in registry_uncertainties
            if isinstance(uncertainty, dict) and isinstance(uncertainty.get("id"), str)
        }
        uncertainties = registry_uncertainties + [
            uncertainty
            for uncertainty in inline_uncertainties
            if uncertainty.get("id") not in registered_uncertainty_ids
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

        for records, kind in ((questions, "question"), (evidence, "evidence"), (claims, "claim"), (knowledge_observations, "observation"), (relationships, "relationship"), (contradictions, "contradiction"), (external_references, "external_reference"), (insights, "insight"), (decisions, "decision"), (rejected_options, "rejected_option"), (requirements, "requirement"), (tests, "acceptance_test")):
            _add_nodes(nodes, records, kind, project_id)
        for records, kind in (
            (hypotheses, "production_hypothesis"),
            (comparisons, "hypothesis_comparison"),
            (uncertainties, "uncertainty"),
            (prototype_plans, "prototype_plan"),
            (prototype_tasks, "prototype_task"),
            (handoffs, "production_handoff"),
            ([{"id": result["result_id"], **result} for result in production_results if isinstance(result, dict) and result.get("result_id")], "production_result"),
            (production_observations, "production_observation"),
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
        for record in knowledge_observations:
            for source in record.get("evidence_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "based_on")
        for record in relationships:
            for field, relation in (("from_id", "relates_from"), ("to_id", "relates_to")):
                source = record.get(field)
                if isinstance(source, str):
                    _add_edge(edges, nodes, project_id, record["id"], source, relation)
            for source in record.get("evidence_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "supported_by")
        for record in contradictions:
            for source in record.get("claim_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "contradicts")
        for record in external_references:
            for source in record.get("evidence_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "supported_by")
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
            for source in record.get("rejected_option_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "rejects")
            for source in record.get("uncertainty_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "has_uncertainty")
        for record in rejected_options:
            for source in record.get("decision_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "rejected_by")
        for record in registry_uncertainties:
            for source in record.get("decision_ids", []):
                _add_edge(edges, nodes, project_id, record["id"], source, "uncertainty_of")
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

        for result in production_results:
            if not isinstance(result, dict) or not isinstance(result.get("result_id"), str):
                continue
            result_id = result["result_id"]
            accepted_handoff = result.get("accepted_handoff") if isinstance(result.get("accepted_handoff"), dict) else {}
            handoff_id = accepted_handoff.get("id")
            if isinstance(handoff_id, str) and node_key(project_id, handoff_id) in nodes:
                _add_edge(edges, nodes, project_id, handoff_id, result_id, "feedback_result")
            for test_result in result.get("test_results", []):
                if isinstance(test_result, dict) and isinstance(test_result.get("acceptance_test_id"), str):
                    _add_edge(edges, nodes, project_id, test_result["acceptance_test_id"], result_id, "evaluated_by_result")
            for observation in result.get("observations", []):
                if not isinstance(observation, dict) or not isinstance(observation.get("id"), str):
                    continue
                observation_node_id = f"{result_id}/{observation['id']}"
                _add_edge(edges, nodes, project_id, result_id, observation_node_id, "contains_observation")
                for requirement_id in observation.get("related_requirement_ids", []):
                    if isinstance(requirement_id, str):
                        _add_edge(edges, nodes, project_id, requirement_id, observation_node_id, "observed_by_result")
                evidence_uri = f"urn:agentic-art-production:result:{result_id}:observations:{observation['id']}"
                for evidence_record in evidence:
                    if evidence_record.get("source_location") == evidence_uri:
                        _add_edge(edges, nodes, project_id, observation_node_id, evidence_record["id"], "derived_as")
            for test_result in result.get("test_results", []):
                if not isinstance(test_result, dict) or not isinstance(test_result.get("acceptance_test_id"), str):
                    continue
                evidence_uri = f"urn:agentic-art-production:result:{result_id}:test_results:{test_result['acceptance_test_id']}"
                for evidence_record in evidence:
                    if evidence_record.get("source_location") == evidence_uri:
                        _add_edge(edges, nodes, project_id, result_id, evidence_record["id"], "derived_test_evidence")

    for signal in _profile_signal_records((profiles_root or (root / "profiles")).resolve()):
        signal_id = signal.get("id")
        creator_id = signal.get("creator_id")
        if not isinstance(signal_id, str) or not isinstance(creator_id, str):
            continue
        profile_id = f"profile/{creator_id}"
        key = node_key(profile_id, signal_id)
        if key in nodes:
            raise ValueError(f"duplicate graph node: {key}")
        nodes[key] = {"key": key, "id": signal_id, "kind": "aesthetic_signal", "project_id": profile_id}
        for field, relation in (("evidence_refs", "based_on"), ("counterexample_refs", "counterexample")):
            for reference in signal.get(field, []):
                if isinstance(reference, str):
                    _add_qualified_edge(edges, nodes, key, reference, relation)

    return {
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [{"from": source, "to": target, "type": kind} for source, target, kind in sorted(edges)],
        "projects": sorted(project_index, key=lambda item: item["id"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic dependency graph from canonical project data.")
    parser.add_argument("--root", type=Path, default=ROOT, help="compatibility alias for --work-root")
    parser.add_argument("--work-root", type=Path, help="project/data work root")
    parser.add_argument("--protocol-root", type=Path, help="read-only protocol root (reserved for shared context)")
    parser.add_argument("--profiles-root", type=Path, help="Read external profile instances from this root (defaults to <root>/profiles).")
    parser.add_argument("--check", action="store_true", help="Fail if generated output is stale.")
    args = parser.parse_args()
    root = (args.work_root or args.root).resolve()
    graph = build_graph(
        root,
        args.profiles_root.resolve() if args.profiles_root else None,
        protocol_root=args.protocol_root.resolve() if args.protocol_root else None,
    )
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
