from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from art_history_adapter import AdapterError, read_snapshot  # noqa: E402


PINNED_COMMIT = "a" * 40


class ArtHistoryAdapterContractTest(unittest.TestCase):
    def make_source(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        (temporary / "data").mkdir()
        graph = {
            "entities": {
                "movement/dada": {
                    "id": "movement/dada",
                    "uri": "urn:ahn:movement/dada",
                    "type": "movement",
                    "label_ja": "ダダ",
                    "label_en": "Dada",
                    "status": "verified",
                    "time": {"start": "1916", "end": "1924"},
                    "space": [{"role": "originated_in", "target": "place/zurich"}],
                    "claims": [{"field": "kind", "source": "https://example.test/dada", "certainty": "attested"}],
                    "sources": ["https://example.test/dada"],
                    "relations": [{"type": "influenced_by", "target": "movement/unknown", "certainty": "hypothesis", "source": "https://example.test/link"}],
                },
                "movement/draft": {
                    "id": "movement/draft",
                    "uri": "urn:ahn:movement/draft",
                    "type": "movement",
                    "label_ja": "下書き",
                    "label_en": "Draft",
                    "status": "draft",
                    "time": {"start": "1924", "end": None},
                    "space": [],
                    "claims": [],
                    "sources": [],
                    "relations": [],
                },
                "place/zurich": {"id": "place/zurich", "type": "place", "region": "europe-central"},
            }
        }
        (temporary / "data" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
        return temporary

    def test_entity_query_pins_commit_and_preserves_uncertainty(self) -> None:
        source = self.make_source()
        result = read_snapshot(source, entity_id="movement/dada", source_commit=PINNED_COMMIT)
        self.assertEqual(PINNED_COMMIT, result["source_commit"])
        self.assertEqual("urn:ahn:movement/dada", result["results"][0]["uri"])
        self.assertEqual("hypothesis", result["results"][0]["relations"][0]["certainty"])
        self.assertEqual([], result["results"][0].get("body", []))

    def test_filters_status_region_century_and_limit(self) -> None:
        source = self.make_source()
        self.assertEqual([], read_snapshot(source, query="Draft", minimum_status="verified", source_commit=PINNED_COMMIT)["results"])
        regional = read_snapshot(source, region="europe-central", source_commit=PINNED_COMMIT)
        self.assertEqual(["movement/dada"], [item["entity_id"] for item in regional["results"]])
        century = read_snapshot(source, century=20, max_results=1, source_commit=PINNED_COMMIT)
        self.assertEqual(1, len(century["results"]))

    def test_invalid_pin_selector_and_missing_graph_fail_closed(self) -> None:
        source = self.make_source()
        with self.assertRaisesRegex(AdapterError, "exactly one"):
            read_snapshot(source, source_commit=PINNED_COMMIT)
        with self.assertRaisesRegex(AdapterError, "40-character"):
            read_snapshot(source, entity_id="movement/dada", source_commit="not-a-sha")
        (source / "data" / "graph.json").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "graph not found"):
            read_snapshot(source, entity_id="movement/dada", source_commit=PINNED_COMMIT)


if __name__ == "__main__":
    unittest.main()
