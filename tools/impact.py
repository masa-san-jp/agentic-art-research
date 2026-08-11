from __future__ import annotations

import argparse
from collections import defaultdict, deque
from pathlib import Path

from _common import ROOT, load_json, stable_json
from build_graph import build_graph


def downstream(graph: dict, start: str) -> list[dict]:
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in graph.get("edges", []):
        adjacency[edge["from"]].append((edge["to"], edge["type"]))
    seen = {start}
    queue = deque([(start, 0)])
    result: list[dict] = []
    while queue:
        current, depth = queue.popleft()
        for target, relation in sorted(adjacency.get(current, [])):
            if target in seen:
                continue
            seen.add(target)
            result.append({"id": target, "depth": depth + 1, "via": relation})
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
    nodes = {item["id"] for item in graph.get("nodes", [])}
    if node not in nodes:
        print(stable_json({"node": node, "found": False, "downstream": []}), end="")
        return 1
    print(stable_json({"node": node, "found": True, "downstream": downstream(graph, node)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

