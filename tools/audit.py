from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, load_yaml, read_jsonl, yaml_list
from build_graph import build_graph


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or value == "unknown":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _audit_project_records(root: Path, graph: dict, now: datetime) -> list[str]:
    try:
        retention = load_yaml(root / "config" / "retention-policy.yaml") or {}
        review_days = int((retention.get("review_days") or {}).get("evidence", 180))
    except (OSError, TypeError, ValueError):
        review_days = 180
    findings: list[str] = []
    for project_entry in graph.get("projects", []):
        project_id = project_entry["id"]
        project = root / project_entry["path"]
        evidence = read_jsonl(project / "02_evidence" / "evidence-ledger.jsonl")
        claims = read_jsonl(project / "03_knowledge" / "claims.jsonl")
        insights = yaml_list(project / "04_decisions" / "insight-register.yaml", "insights")
        decisions = yaml_list(project / "04_decisions" / "decision-log.yaml", "decisions")
        requirements = yaml_list(project / "05_production" / "production-requirements.yaml", "requirements")
        acceptance_tests = yaml_list(project / "05_production" / "acceptance-tests.yaml", "acceptance_tests")

        source_types = Counter(record.get("source_type") for record in evidence if record.get("source_type"))
        if len(evidence) >= 2 and source_types:
            dominant_type, dominant_count = source_types.most_common(1)[0]
            if dominant_count / len(evidence) >= 0.8:
                findings.append(
                    f"BIAS: project {project_id} has concentrated evidence source_type {dominant_type!r} "
                    f"({dominant_count}/{len(evidence)})"
                )

        for record in evidence:
            acquired_at = _parse_timestamp(record.get("acquired_at"))
            if acquired_at is not None and (now - acquired_at).days > review_days:
                findings.append(
                    f"STALE-EVIDENCE: evidence {record.get('id')} acquired_at {record.get('acquired_at')} "
                    f"exceeds the {review_days}-day review window"
                )
        for record in claims:
            review_after = _parse_timestamp(record.get("review_after"))
            if review_after is not None and review_after < now:
                findings.append(f"STALE-EVIDENCE: claim {record.get('id')} review_after {record.get('review_after')} has passed")

        for record in insights:
            if record.get("claim_ids") and not record.get("opposing_claim_ids"):
                findings.append(f"NO-COUNTEREVIDENCE: insight {record.get('id')} has no opposing claim search recorded")

        for record in decisions:
            status = record.get("status")
            if status in {"PROPOSED", "ON_HOLD", "INVALIDATED"}:
                findings.append(f"WEAK-DECISION: decision {record.get('id')} has status {status}")
            basis = set(record.get("insight_ids", [])) | set(record.get("evidence_ids", []))
            if status == "ADOPTED" and len(basis) <= 1:
                findings.append(f"SINGLE-SOURCE: decision {record.get('id')} has only {len(basis)} direct basis object")

        tests_by_id = {record.get("id"): record for record in acceptance_tests}
        for requirement in requirements:
            if requirement.get("priority") != "mandatory":
                continue
            for test_id in requirement.get("acceptance_test_ids", []):
                test = tests_by_id.get(test_id)
                if not test or test.get("result") != "PASS":
                    result = test.get("result", "MISSING") if test else "MISSING"
                    findings.append(f"UNRUN-TEST: mandatory requirement {requirement.get('id')} acceptance test {test_id} is {result}")
    return findings


def audit_graph(graph: dict, root: Path | None = None, now: datetime | None = None) -> list[str]:
    incoming = Counter(edge["to"] for edge in graph.get("edges", []))
    outgoing = Counter(edge["from"] for edge in graph.get("edges", []))
    findings: list[str] = []
    for node in graph.get("nodes", []):
        identifier = node["id"]
        key = node.get("key", identifier)
        kind = node["kind"]
        if kind in {"claim", "insight", "decision", "requirement", "acceptance_test"} and incoming[key] == 0:
            findings.append(f"ORPHAN: {kind} {identifier} has no upstream basis")
        if kind == "evidence" and outgoing[key] == 0:
            findings.append(f"UNUSED: evidence {identifier} has no downstream use")
        if kind == "requirement" and outgoing[key] == 0:
            findings.append(f"UNTESTED: requirement {identifier} has no acceptance test")
    if root is not None:
        audit_now = now or datetime.now(timezone.utc)
        findings.extend(_audit_project_records(root, graph, audit_now))
    return sorted(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run non-blocking repository audit.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    findings = audit_graph(build_graph(root), root=root)
    lines = ["# Audit report", "", f"Findings: {len(findings)}", ""]
    lines.extend(f"- {finding}" for finding in findings)
    if not findings:
        lines.append("- No structural findings in current data.")
    content = "\n".join(lines) + "\n"
    if args.stdout:
        print(content, end="")
    else:
        output = root / "data" / "audit-report.md"
        atomic_write_text(output, content)
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
