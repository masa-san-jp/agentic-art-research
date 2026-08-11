from __future__ import annotations

import argparse
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from _common import ROOT, load_json, stable_json
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


def downstream(graph: dict, start: str) -> list[dict]:
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in graph.get("edges", []):
        adjacency[edge["from"]].append((edge["to"], edge["type"]))
    start_key = resolve_node_key(graph, start)
    if start_key is None:
        return []
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Report downstream impact of changed evidence or another node.")
    parser.add_argument("--evidence", dest="node")
    parser.add_argument("--node")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    node = args.node
    if not node:
        parser.error("provide --evidence or --node")
    root = args.root.resolve()
    graph_path = root / "data" / "dependency-graph.json"
    graph = load_json(graph_path) if graph_path.exists() else build_graph(root)
    if resolve_node_key(graph, node) is None:
        print(stable_json({"node": node, "found": False, "downstream": []}), end="")
        return 1
    print(stable_json({"node": node, "found": True, "downstream": downstream(graph, node)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
