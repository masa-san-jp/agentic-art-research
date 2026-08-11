from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REFERENCE_ID_RE = re.compile(r"^XR([0-9]{3,})$")


class ArtHistoryAdapterError(ValueError):
    """Raised when the external KB cannot satisfy the read-only adapter contract."""


class ArtHistoryNotesAdapter:
    def __init__(self, source_root: Path, *, repository_root: Path = ROOT, source_commit: str | None = None) -> None:
        self.source_root = source_root.resolve()
        self.repository_root = repository_root.resolve()
        if not self.source_root.is_dir():
            raise FileNotFoundError(f"art-history-notes source not found: {self.source_root}")
        self.config = self._load_config()
        self.status_order = self.config.get("status_order")
        if not isinstance(self.status_order, list) or not self.status_order or any(not isinstance(item, str) for item in self.status_order):
            raise ArtHistoryAdapterError("config/integrations.yaml: status_order must be a non-empty list")
        self._status_rank = {status: index for index, status in enumerate(self.status_order)}
        if len(self._status_rank) != len(self.status_order):
            raise ArtHistoryAdapterError("config/integrations.yaml: status_order contains duplicates")
        uri_prefix = self.config.get("uri_prefix")
        if not isinstance(uri_prefix, str) or not uri_prefix:
            raise ArtHistoryAdapterError("config/integrations.yaml: uri_prefix must be non-empty")
        self.uri_prefix = uri_prefix
        self.source_commit = source_commit or self._git_head()
        if not COMMIT_RE.fullmatch(self.source_commit):
            raise ArtHistoryAdapterError("art-history-notes source commit must be a 40-character lowercase SHA")
        graph_path = self.source_root / "data" / "graph.json"
        self.graph = load_json(graph_path)
        if not isinstance(self.graph, dict) or not isinstance(self.graph.get("entities"), dict):
            raise ArtHistoryAdapterError(f"{graph_path}: graph must contain an entities mapping")
        self.entities: dict[str, dict[str, Any]] = {}
        for entity_id, entity in self.graph["entities"].items():
            if not isinstance(entity_id, str) or not isinstance(entity, dict):
                raise ArtHistoryAdapterError(f"{graph_path}: invalid entity record")
            if entity.get("id") != entity_id:
                raise ArtHistoryAdapterError(f"{graph_path}: entity key and id differ for {entity_id}")
            status = entity.get("status")
            if status not in self._status_rank:
                raise ArtHistoryAdapterError(f"{graph_path}: unknown entity status {status!r} for {entity_id}")
            self.entities[entity_id] = entity
        self.edges = self.graph.get("edges", [])
        if not isinstance(self.edges, list) or any(not isinstance(edge, dict) for edge in self.edges):
            raise ArtHistoryAdapterError(f"{graph_path}: edges must be a list of objects")

    def _load_config(self) -> dict[str, Any]:
        document = load_yaml(self.repository_root / "config" / "integrations.yaml") or {}
        config = document.get("art_history_notes") if isinstance(document, dict) else None
        if not isinstance(config, dict):
            raise ArtHistoryAdapterError("config/integrations.yaml: art_history_notes must be a mapping")
        if config.get("system") != "art-history-notes":
            raise ArtHistoryAdapterError("config/integrations.yaml: art_history_notes.system is invalid")
        return config

    def _git_head(self) -> str:
        git_dir = self.source_root / ".git"
        if not git_dir.exists():
            raise ArtHistoryAdapterError("art-history-notes source has no .git directory; source commit is required")
        try:
            result = subprocess.run(
                ["git", "-C", str(self.source_root), "rev-parse", "--verify", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ArtHistoryAdapterError("could not read art-history-notes source commit") from exc
        return result.stdout.strip()

    def _minimum_status(self, value: str | None) -> str:
        minimum = value or self.config.get("default_minimum_status")
        if not isinstance(minimum, str) or minimum not in self._status_rank:
            raise ArtHistoryAdapterError(f"unknown minimum status: {minimum!r}")
        return minimum

    def _eligible(self, entity: dict[str, Any], minimum_status: str) -> bool:
        return self._status_rank[entity["status"]] >= self._status_rank[minimum_status]

    def _public_entity(self, entity: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "id",
            "uri",
            "type",
            "label_ja",
            "label_en",
            "status",
            "updated",
            "sources",
            "claims",
            "relations",
            "time",
            "space",
            "authority",
        )
        return {field: entity[field] for field in fields if field in entity}

    def lookup(self, entity_id: str, *, minimum_status: str | None = None) -> dict[str, Any]:
        if not isinstance(entity_id, str) or not entity_id:
            raise ArtHistoryAdapterError("entity_id must be a non-empty string")
        minimum = self._minimum_status(minimum_status)
        entity = self.entities.get(entity_id)
        if entity is None:
            raise FileNotFoundError(f"art-history-notes entity not found: {entity_id}")
        if not self._eligible(entity, minimum):
            raise ArtHistoryAdapterError(f"entity {entity_id} has status {entity['status']}, below minimum {minimum}")
        return {"source_commit": self.source_commit, "entity": self._public_entity(entity)}

    def search(self, query: str, *, minimum_status: str | None = None, limit: int | None = None) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ArtHistoryAdapterError("search query must be non-empty")
        minimum = self._minimum_status(minimum_status)
        default_limit = self.config.get("default_max_results")
        result_limit = default_limit if limit is None else limit
        if not isinstance(result_limit, int) or isinstance(result_limit, bool) or result_limit <= 0:
            raise ArtHistoryAdapterError("result limit must be a positive integer")
        needle = query.casefold()
        matches = []
        for entity_id in sorted(self.entities):
            entity = self.entities[entity_id]
            if not self._eligible(entity, minimum):
                continue
            haystack = " ".join(str(entity.get(field, "")) for field in ("id", "label_ja", "label_en"))
            if needle not in haystack.casefold():
                continue
            matches.append(
                {
                    "id": entity_id,
                    "uri": entity.get("uri"),
                    "label_ja": entity.get("label_ja"),
                    "label_en": entity.get("label_en"),
                    "status": entity.get("status"),
                }
            )
            if len(matches) >= result_limit:
                break
        return {"source_commit": self.source_commit, "query": query, "results": matches}

    def bundle(self, entity_id: str, *, depth: int = 1, minimum_status: str | None = None) -> dict[str, Any]:
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
            raise ArtHistoryAdapterError("bundle depth must be a non-negative integer")
        minimum = self._minimum_status(minimum_status)
        center = self.lookup(entity_id, minimum_status=minimum)["entity"]
        neighbors: dict[str, int] = {entity_id: 0}
        queue = deque([entity_id])
        while queue:
            current = queue.popleft()
            current_depth = neighbors[current]
            if current_depth >= depth:
                continue
            for edge in sorted(self.edges, key=lambda item: (str(item.get("from")), str(item.get("to")), str(item.get("type")))):
                source = edge.get("from")
                target = edge.get("to")
                if source != current and target != current:
                    continue
                other = target if source == current else source
                if not isinstance(other, str) or other not in self.entities or other in neighbors:
                    continue
                if not self._eligible(self.entities[other], minimum):
                    continue
                neighbors[other] = current_depth + 1
                queue.append(other)
        ids = sorted(neighbors)
        selected_edges = [
            edge
            for edge in self.edges
            if edge.get("from") in neighbors and edge.get("to") in neighbors
        ]
        selected_edges.sort(key=lambda item: (str(item.get("from")), str(item.get("to")), str(item.get("type"))))
        return {
            "source_commit": self.source_commit,
            "center_id": entity_id,
            "entities": [self._public_entity(self.entities[entity]) for entity in ids],
            "edges": selected_edges,
        }

    def external_reference(self, entity_id: str, *, reference_id: str, acquired_at: str, usage: str) -> dict[str, Any]:
        if not REFERENCE_ID_RE.fullmatch(reference_id):
            raise ArtHistoryAdapterError(f"invalid external reference ID: {reference_id}")
        if not isinstance(usage, str) or not usage.strip():
            raise ArtHistoryAdapterError("usage must be a non-empty string")
        _validate_timestamp(acquired_at)
        entity = self.lookup(entity_id)["entity"]
        uri = entity.get("uri")
        if not isinstance(uri, str) or not uri.startswith(self.uri_prefix):
            raise ArtHistoryAdapterError(f"entity {entity_id} has no stable URI")
        return {
            "id": reference_id,
            "system": self.config["system"],
            "entity_id": entity_id,
            "uri": uri,
            "source_commit": self.source_commit,
            "acquired_at": acquired_at,
            "usage": usage,
        }


def _validate_timestamp(value: str) -> None:
    normalized = value[:-1] + "+00:00" if isinstance(value, str) and value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ArtHistoryAdapterError("acquired_at must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArtHistoryAdapterError("acquired_at must include a timezone")


def _project(root: Path, target: str) -> Path:
    if not isinstance(target, str) or not target.startswith("project/") or target.count("/") != 1:
        raise ArtHistoryAdapterError("target must be project/<slug>")
    projects_root = (root / "projects").resolve()
    project = (projects_root / target.split("/", 1)[1]).resolve()
    if project.parent != projects_root or not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def import_references(
    root: Path,
    target: str,
    adapter: ArtHistoryNotesAdapter,
    entity_ids: list[str],
    *,
    acquired_at: str,
    usage: str,
) -> dict[str, Any]:
    if len(set(entity_ids)) != len(entity_ids):
        raise ArtHistoryAdapterError("entity_ids contains duplicates")
    project = _project(root.resolve(), target)
    path = project / "03_knowledge/external-references.jsonl"
    before = path.read_text(encoding="utf-8")
    existing = read_jsonl(path)
    used_ids = set()
    for record in existing:
        record_id = record.get("id")
        if not isinstance(record_id, str) or record_id in used_ids:
            raise ArtHistoryAdapterError("external reference IDs must be unique and non-empty")
        used_ids.add(record_id)
    next_number = max((int(match.group(1)) for match in (REFERENCE_ID_RE.fullmatch(value) for value in used_ids) if match), default=0) + 1
    imported: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entity_id in sorted(entity_ids):
        existing_match = next(
            (
                record
                for record in existing
                if record.get("system") == "art-history-notes"
                and record.get("entity_id") == entity_id
                and record.get("source_commit") == adapter.source_commit
                and record.get("usage") == usage
            ),
            None,
        )
        if existing_match is not None:
            skipped.append(entity_id)
            continue
        reference_id = f"XR{next_number:03d}"
        next_number += 1
        imported.append(adapter.external_reference(entity_id, reference_id=reference_id, acquired_at=acquired_at, usage=usage))
    if imported:
        atomic_write_text(path, before + "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in imported))
    return {"source_commit": adapter.source_commit, "imported": imported, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read art-history-notes without copying its canonical entity data.")
    parser.add_argument("command", choices=["search", "lookup", "bundle", "import"])
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--entity-id")
    parser.add_argument("--query")
    parser.add_argument("--minimum-status")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--target")
    parser.add_argument("--acquired-at")
    parser.add_argument("--usage")
    parser.add_argument("--entity-ids", nargs="+")
    args = parser.parse_args()
    try:
        adapter = ArtHistoryNotesAdapter(args.source_root, repository_root=args.root.resolve())
        if args.command == "search":
            if not args.query:
                parser.error("search requires --query")
            result = adapter.search(args.query, minimum_status=args.minimum_status, limit=args.limit)
        elif args.command == "lookup":
            if not args.entity_id:
                parser.error("lookup requires --entity-id")
            result = adapter.lookup(args.entity_id, minimum_status=args.minimum_status)
        elif args.command == "bundle":
            if not args.entity_id:
                parser.error("bundle requires --entity-id")
            result = adapter.bundle(args.entity_id, depth=args.depth, minimum_status=args.minimum_status)
        else:
            if not args.target or not args.entity_ids or not args.acquired_at or not args.usage:
                parser.error("import requires --target, --entity-ids, --acquired-at, and --usage")
            result = import_references(
                args.root.resolve(),
                args.target,
                adapter,
                args.entity_ids,
                acquired_at=args.acquired_at,
                usage=args.usage,
            )
    except (ArtHistoryAdapterError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
