from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from art_history_adapter import ArtHistoryAdapterError, ArtHistoryNotesAdapter, import_references
from new_project import create_project
from validate import validate_repository


class ArtHistoryAdapterContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def make_source(self, root: Path) -> Path:
        source = root / "art-history-notes"
        (source / "data").mkdir(parents=True)
        (source / "data/graph.json").write_text(
            json.dumps(
                {
                    "entities": {
                        "movement/alpha": {
                            "id": "movement/alpha",
                            "uri": "urn:ahn:movement/alpha",
                            "type": "movement",
                            "label_ja": "アルファ",
                            "label_en": "Alpha",
                            "status": "draft",
                            "updated": "2026-08-10",
                            "sources": ["https://example.invalid/alpha"],
                            "relations": [],
                        },
                        "movement/beta": {
                            "id": "movement/beta",
                            "uri": "urn:ahn:movement/beta",
                            "type": "movement",
                            "label_ja": "ベータ",
                            "label_en": "Beta",
                            "status": "verified",
                            "updated": "2026-08-10",
                            "sources": ["https://example.invalid/beta"],
                            "relations": [],
                        },
                        "movement/stub": {
                            "id": "movement/stub",
                            "uri": "urn:ahn:movement/stub",
                            "type": "movement",
                            "label_ja": "スタブ",
                            "label_en": "Stub",
                            "status": "stub",
                            "updated": "2026-08-10",
                            "sources": [],
                            "relations": [],
                        },
                    },
                    "edges": [{"from": "movement/alpha", "to": "movement/beta", "type": "influenced_by"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return source

    def test_search_bundle_and_reference_import_are_read_only_and_idempotent(self) -> None:
        root = self.make_root()
        source = self.make_source(root)
        adapter = ArtHistoryNotesAdapter(source, repository_root=root, source_commit="a" * 40)
        search = adapter.search("a", minimum_status="draft", limit=10)
        self.assertEqual(["movement/alpha", "movement/beta"], [item["id"] for item in search["results"]])
        bundle = adapter.bundle("movement/alpha", depth=1)
        self.assertEqual("a" * 40, bundle["source_commit"])
        self.assertEqual(["movement/alpha", "movement/beta"], [item["id"] for item in bundle["entities"]])
        self.assertNotIn("label_en", bundle["edges"][0])
        with self.assertRaisesRegex(ArtHistoryAdapterError, "below minimum"):
            adapter.lookup("movement/stub")

        create_project(root, "adapter-test", "Adapter Test", created_at="2026-08-11T00:00:00+09:00")
        imported = import_references(
            root,
            "project/adapter-test",
            adapter,
            ["movement/beta", "movement/alpha"],
            acquired_at="2026-08-11T00:10:00+09:00",
            usage="precedent_research",
        )
        repeated = import_references(
            root,
            "project/adapter-test",
            adapter,
            ["movement/beta", "movement/alpha"],
            acquired_at="2026-08-11T00:10:00+09:00",
            usage="precedent_research",
        )
        self.assertEqual(["movement/alpha", "movement/beta"], [item["entity_id"] for item in imported["imported"]])
        self.assertEqual([], repeated["imported"])
        self.assertEqual(["movement/alpha", "movement/beta"], repeated["skipped"])
        text = (root / "projects/adapter-test/03_knowledge/external-references.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("アルファ", text)
        self.assertEqual([], validate_repository(root))

    def test_missing_source_commit_is_rejected(self) -> None:
        root = self.make_root()
        source = self.make_source(root)
        with self.assertRaisesRegex(ArtHistoryAdapterError, "no .git"):
            ArtHistoryNotesAdapter(source, repository_root=root)


if __name__ == "__main__":
    unittest.main()
