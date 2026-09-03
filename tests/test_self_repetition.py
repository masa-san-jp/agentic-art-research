from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from self_repetition import apply_report, scan_projects


class SelfRepetitionScanTest(unittest.TestCase):
    def make_fixture(self) -> tuple[Path, Path, Path]:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        history = temporary / "history"
        candidate = temporary / "candidate"
        (candidate / "05_production").mkdir(parents=True)
        (candidate / "manifest.yaml").write_text(
            "project:\n  id: project/new-candidate\n",
            encoding="utf-8",
        )
        (candidate / "05_production/creative-direction.md").write_text(
            "# Candidate\n\nclose-but-cannot-reach is the selected claim.\n",
            encoding="utf-8",
        )
        for name in ("close-but-cannot-reach", "harmony-proof", "auto-auto-plan-repository-202", "yohaku-no-iki"):
            project = history / name / "05_production"
            project.mkdir(parents=True)
            (project.parent / "manifest.yaml").write_text(
                f"project:\n  id: production/{name}\n",
                encoding="utf-8",
            )
            (project / "creative-direction.md").write_text(
                f"# {name}\n\nThe project tests close-but-cannot-reach through a distinct arrangement.\n",
                encoding="utf-8",
            )
        return candidate, history, temporary

    def test_four_prior_projects_are_detected_with_high_risk(self) -> None:
        candidate, history, _ = self.make_fixture()
        report = scan_projects(
            candidate,
            history,
            repository="masa-san-jp/agentic-art-research",
            source_commit="ce7e214f22e25c277c8f7277d83cd8cd85a3a8c1",
            now="2026-09-03T10:00:00+09:00",
        )
        self.assertEqual("self-repetition-scan/v1", report["contract_version"])
        self.assertEqual(4, report["scanned_project_count"])
        self.assertEqual("HIGH", report["risk_level"])
        self.assertEqual(4, len({item["prior_signal_ref"].split("::", 1)[0] for item in report["matches"]}))
        self.assertTrue(all("close-but-cannot-reach" in item["matched_terms"] for item in report["matches"]))
        schema = json.loads((ROOT / "schemas/self-repetition-scan.schema.json").read_text(encoding="utf-8"))
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(report)))

    def test_empty_history_is_observed_low_not_unknown(self) -> None:
        candidate, _, temporary = self.make_fixture()
        empty = temporary / "empty"
        empty.mkdir()
        report = scan_projects(
            candidate,
            empty,
            repository="masa-san-jp/agentic-art-research",
            source_commit="ce7e214f22e25c277c8f7277d83cd8cd85a3a8c1",
            now="2026-09-03T10:00:00+09:00",
        )
        self.assertEqual("LOW", report["risk_level"])
        self.assertEqual([], report["matches"])

    def test_apply_replaces_marker_idempotently_and_keeps_metadata_only_refs(self) -> None:
        candidate, history, _ = self.make_fixture()
        report = scan_projects(
            candidate,
            history,
            repository="masa-san-jp/agentic-art-research",
            source_commit="ce7e214f22e25c277c8f7277d83cd8cd85a3a8c1",
            now="2026-09-03T10:00:00+09:00",
        )
        apply_report(candidate, report)
        first = (candidate / "05_production/creative-direction.md").read_text(encoding="utf-8")
        apply_report(candidate, report)
        second = (candidate / "05_production/creative-direction.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(1, second.count("## self_repetition_risk"))
        self.assertIn("risk_level: `HIGH`", second)
        self.assertNotIn(str(candidate), second)
        self.assertNotIn("The project tests", second)

    def test_v2_scopes_history_to_creator_and_reads_visual_language_mechanism(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        candidate = temporary / "candidate"
        history = temporary / "history"

        def write_project(root: Path, project_id: str, creator_id: str, mechanism: str) -> None:
            production = root / "05_production"
            production.mkdir(parents=True)
            (root / "manifest.yaml").write_text(
                f"project:\n  id: {project_id}\n  creator_id: {creator_id}\n",
                encoding="utf-8",
            )
            (production / "visual-language.yaml").write_text(
                "schema_version: 1.1.0\n"
                "techniques:\n"
                "  - id: VT001\n"
                "    name: interval cut\n"
                "    intent: expose a delayed response\n"
                f"    mechanism: {mechanism}\n"
                "    proposition_component_ids: [DC001]\n"
                "    reference_ids: [PA001]\n",
                encoding="utf-8",
            )

        write_project(candidate, "project/new-candidate", "creator/one", "shared interval mechanism")
        write_project(history / "same", "production/same", "creator/one", "shared interval mechanism")
        write_project(history / "other", "production/other", "creator/two", "shared interval mechanism")

        report = scan_projects(
            candidate,
            history,
            repository="masa-san-jp/agentic-art-research",
            source_commit="ce7e214f22e25c277c8f7277d83cd8cd85a3a8c1",
            now="2026-09-03T10:00:00+09:00",
            contract_version="self-repetition-scan/v2",
        )
        self.assertEqual("self-repetition-scan/v2", report["contract_version"])
        self.assertEqual({"status": "AVAILABLE", "reason_code": None}, report["history_access"])
        self.assertEqual(1, report["scanned_project_count"])
        self.assertTrue(any("visual-language.yaml" in item["prior_signal_ref"] for item in report["matches"]))
        schema = json.loads((ROOT / "schemas/self-repetition-scan-v2.schema.json").read_text(encoding="utf-8"))
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(report)))

    def test_v2_records_unavailable_history_as_unknown(self) -> None:
        candidate, _, temporary = self.make_fixture()
        report = scan_projects(
            candidate,
            temporary / "does-not-exist",
            repository="masa-san-jp/agentic-art-research",
            source_commit="ce7e214f22e25c277c8f7277d83cd8cd85a3a8c1",
            now="2026-09-03T10:00:00+09:00",
            contract_version="self-repetition-scan/v2",
        )
        self.assertEqual("UNKNOWN", report["risk_level"])
        self.assertEqual("UNAVAILABLE", report["history_access"]["status"])
        self.assertIn("参照できない", report["assessment"])
        self.assertNotEqual("MEDIUM", report["risk_level"])
        schema = json.loads((ROOT / "schemas/self-repetition-scan-v2.schema.json").read_text(encoding="utf-8"))
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(report)))
