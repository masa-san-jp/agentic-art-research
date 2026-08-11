from __future__ import annotations

import argparse
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, load_json, stable_json
from build_graph import build_graph


def resolve_node_key(graph: dict[str, Any], identifier: str) -> str | None:
    nodes = graph.get("nodes", [])
    exact = {node.get("key") for node in nodes if node.get("key") == identifier}
    if exact:
        return next(iter(exact))
    matches = [node["key"] for node in nodes if node.get("id") == identifier and node.get("key")]
    if len(matches) == 1:
        return matches[0]
    return None


def _adjacency(graph: dict[str, Any], *, reverse: bool = False) -> dict[str, list[tuple[str, str]]]:
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in graph.get("edges", []):
        source = edge["to"] if reverse else edge["from"]
        target = edge["from"] if reverse else edge["to"]
        adjacency[source].append((target, edge["type"]))
    return adjacency


def _traverse(graph: dict[str, Any], start: str, *, reverse: bool = False) -> list[dict]:
    start_key = resolve_node_key(graph, start)
    if start_key is None:
        return []
    adjacency = _adjacency(graph, reverse=reverse)
    node_by_key = {node["key"]: node for node in graph.get("nodes", []) if node.get("key")}
    seen = {start_key}
    queue = deque([(start_key, 0)])
    result: list[dict] = []
    while queue:
        current, depth = queue.popleft()
        for target, relation in sorted(adjacency.get(current, [])):
            if target in seen:
                continue
            seen.add(target)
            target_node = node_by_key.get(target)
            if target_node is None:
                continue
            result.append(
                {
                    "id": target_node["id"],
                    "key": target,
                    "project_id": target_node["project_id"],
                    "depth": depth + 1,
                    "via": relation,
                }
            )
            queue.append((target, depth + 1))
    return result


def downstream(graph: dict, start: str) -> list[dict]:
    return _traverse(graph, start)


def upstream(graph: dict, start: str) -> list[dict]:
    return _traverse(graph, start, reverse=True)


def impact_report(graph: dict[str, Any], start: str) -> dict[str, Any]:
    resolved_key = resolve_node_key(graph, start)
    if resolved_key is None:
        return {"node": start, "found": False, "upstream": [], "downstream": []}
    return {
        "node": start,
        "resolved_key": resolved_key,
        "found": True,
        "upstream": upstream(graph, start),
        "downstream": downstream(graph, start),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Impact report: {report['node']}", ""]
    if not report.get("found"):
        lines.extend(["- Found: no", "", "The requested node is missing or its local ID is ambiguous.", ""])
        return "\n".join(lines)
    lines.extend([f"- Found: yes", f"- Resolved key: `{report['resolved_key']}`", ""])
    for direction in ("upstream", "downstream"):
        lines.extend([f"## {direction.title()}", ""])
        entries = report[direction]
        if not entries:
            lines.append("- None")
        else:
            for item in entries:
                lines.append(
                    f"- `{item['id']}` (`{item['project_id']}`, depth {item['depth']}, via `{item['via']}`)"
                )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report downstream impact of changed evidence or another node.")
    parser.add_argument("--evidence", dest="node")
    parser.add_argument("--node")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    node = args.node
    if not node:
        parser.error("provide --evidence or --node")
    root = args.root.resolve()
    graph_path = root / "data" / "dependency-graph.json"
    graph = load_json(graph_path) if graph_path.exists() else build_graph(root)
    report = impact_report(graph, node)
    content = stable_json(report) if args.format == "json" else render_markdown(report)
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0 if report["found"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
