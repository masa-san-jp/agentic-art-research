from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from _common import ROOT, atomic_write_text
from build_graph import build_graph


def audit_graph(graph: dict) -> list[str]:
    incoming = Counter(edge["to"] for edge in graph.get("edges", []))
    outgoing = Counter(edge["from"] for edge in graph.get("edges", []))
    findings: list[str] = []
    for node in graph.get("nodes", []):
        identifier = node["id"]
        kind = node["kind"]
        if kind in {"claim", "insight", "decision", "requirement", "acceptance_test"} and incoming[identifier] == 0:
            findings.append(f"ORPHAN: {kind} {identifier} has no upstream basis")
        if kind == "evidence" and outgoing[identifier] == 0:
            findings.append(f"UNUSED: evidence {identifier} has no downstream use")
        if kind == "requirement" and outgoing[identifier] == 0:
            findings.append(f"UNTESTED: requirement {identifier} has no acceptance test")
    return sorted(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run non-blocking repository audit.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    findings = audit_graph(build_graph(root))
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

