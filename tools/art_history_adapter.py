from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from _common import atomic_write_text, stable_json


SYSTEM = "art-history-notes"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
STATUS_RANK = {"stub": 0, "draft": 1, "verified": 2}


class AdapterError(ValueError):
    pass


def _source_commit(root: Path, expected: str | None) -> str:
    if expected is not None and not SHA_RE.fullmatch(expected):
        raise AdapterError("source_commit must be a 40-character lowercase git SHA")
    actual: str | None = None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        actual = completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        actual = None
    if actual is not None and not SHA_RE.fullmatch(actual):
        raise AdapterError("source checkout returned an invalid HEAD SHA")
    if expected is not None and actual is not None and expected != actual:
        raise AdapterError(f"source commit mismatch: expected {expected}, checkout is {actual}")
    if expected is not None:
        return expected
    if actual is None:
        raise AdapterError("source_commit is required when root is not a git checkout")
    return actual


def _load_graph(root: Path) -> dict[str, Any]:
    path = root / "data" / "graph.json"
    if not path.is_file():
        raise FileNotFoundError(f"art-history-notes graph not found: {path}")
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AdapterError(f"invalid art-history-notes graph: {exc.msg}") from exc
    if not isinstance(graph, dict) or not isinstance(graph.get("entities"), dict):
        raise AdapterError("art-history-notes graph must contain an entities object")
    return graph


def _year(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(-?\d{3,4})", value)
    if not match:
        return None
    return int(match.group(1))


def _century(year: int | None) -> int | None:
    if year is None:
        return None
    if year < 0:
        return -(abs(year) // 100 + 1)
    return year // 100 + 1


def _regions(entity: dict[str, Any], entities: dict[str, Any]) -> set[str]:
    regions: set[str] = set()
    for space in entity.get("space") or []:
        if not isinstance(space, dict) or space.get("role") != "originated_in":
            continue
        place = entities.get(space.get("target")) or {}
        region = place.get("region")
        if isinstance(region, str) and region:
            regions.add(region)
    return regions


def _result(entity_id: str, entity: dict[str, Any], source_commit: str) -> dict[str, Any]:
    relations = []
    for relation in entity.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        relations.append(
            {
                key: relation[key]
                for key in ("type", "target", "certainty", "source")
                if key in relation
            }
        )
    return {
        "entity_id": entity_id,
        "uri": entity.get("uri"),
        "label": {"ja": entity.get("label_ja"), "en": entity.get("label_en")},
        "status": entity.get("status"),
        "claims": entity.get("claims") or [],
        "sources": entity.get("sources") or [],
        "relations": relations,
    }


def read_snapshot(
    root: Path,
    *,
    entity_id: str | None = None,
    query: str | None = None,
    region: str | None = None,
    century: int | None = None,
    minimum_status: str = "stub",
    max_results: int = 20,
    source_commit: str | None = None,
) -> dict[str, Any]:
    filters = [entity_id is not None, query is not None, region is not None, century is not None]
    if sum(filters) != 1:
        raise AdapterError("exactly one of entity_id, query, region, or century is required")
    if minimum_status not in STATUS_RANK:
        raise AdapterError(f"unknown minimum status: {minimum_status}")
    if max_results < 1:
        raise AdapterError("max_results must be positive")

    pinned = _source_commit(root, source_commit)
    graph = _load_graph(root)
    entities = graph["entities"]
    candidates: list[tuple[str, dict[str, Any]]] = []
    query_lower = query.lower() if query is not None else None
    for candidate_id, candidate in sorted(entities.items()):
        if not isinstance(candidate, dict):
            continue
        status = candidate.get("status")
        if status not in STATUS_RANK or STATUS_RANK[status] < STATUS_RANK[minimum_status]:
            continue
        if entity_id is not None and candidate_id != entity_id:
            continue
        if query_lower is not None:
            haystack = " ".join(
                str(candidate.get(key) or "") for key in ("id", "uri", "label_ja", "label_en")
            ).lower()
            if query_lower not in haystack:
                continue
        if region is not None and region not in _regions(candidate, entities):
            continue
        if century is not None and _century(_year((candidate.get("time") or {}).get("start"))) != century:
            continue
        candidates.append((candidate_id, candidate))

    return {
        "system": SYSTEM,
        "source_commit": pinned,
        "filter": {
            "entity_id": entity_id,
            "query": query,
            "region": region,
            "century": century,
            "minimum_status": minimum_status,
            "max_results": max_results,
        },
        "results": [_result(entity_id, entity, pinned) for entity_id, entity in candidates[:max_results]],
        "truncated": len(candidates) > max_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a pinned art-history-notes graph snapshot.")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--entity-id")
    selector.add_argument("--query")
    selector.add_argument("--region")
    selector.add_argument("--century", type=int)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--minimum-status", choices=sorted(STATUS_RANK), default="stub")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        content = stable_json(
            read_snapshot(
                args.root.resolve(),
                entity_id=args.entity_id,
                query=args.query,
                region=args.region,
                century=args.century,
                minimum_status=args.minimum_status,
                max_results=args.max_results,
                source_commit=args.source_commit,
            )
        )
    except (AdapterError, FileNotFoundError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
