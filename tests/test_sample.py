from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "harmony"
sys.path.insert(0, str(REPO_ROOT / "tools"))

from audit import audit_graph
from build_graph import build_graph
from bundle import build_bundle
from impact import impact_report
from run_project import run_offline_fixture
from validate import validate_repository


class SampleProjectContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_offline_fixture_recreates_complete_with_gaps_traceable_package(self) -> None:
        root = self.make_root()
        project = run_offline_fixture(root, "harmony-study", FIXTURE)

        self.assertEqual([], validate_repository(root))
        report = json.loads((project / "07_runtime" / "completion-report.json").read_text(encoding="utf-8"))
        self.assertEqual("COMPLETE_WITH_GAPS", report["status"])
        self.assertEqual([], audit_graph(build_graph(root), root=root, now=datetime(2026, 8, 11, tzinfo=timezone.utc)))

        graph = build_graph(root)
        self.assertEqual(graph, build_graph(root))
        impact = impact_report(graph, "EV001")
        self.assertTrue(impact["found"])
        self.assertEqual(
            {"CL001", "CL002", "IN001", "DC001", "DC002", "RQ001", "AT001"},
            {item["id"] for item in impact["downstream"]},
        )
        self.assertIn("RQ001", {item["id"] for item in impact["downstream"]})
        self.assertEqual({"Q001"}, {item["id"] for item in impact["upstream"]})

        human_bundle = build_bundle(root, "project/harmony-study", "human")
        production_bundle = build_bundle(root, "project/harmony-study", "production-agent")
        self.assertIn("Harmony Study", human_bundle)
        self.assertIn("RQ001", production_bundle)
        self.assertIn("AT001", production_bundle)
        self.assertNotIn("EV001", production_bundle)

        graph_output = (root / "data" / "dependency-graph.json").read_text(encoding="utf-8")
        self.assertEqual(graph_output, json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
