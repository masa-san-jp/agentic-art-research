from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from canonical import canonical_sha256
from export_handoff import export_handoff
from import_production_result import ResultImportError, import_production_result, result_sha256
from run_project import run_offline_fixture
from test_feedback_import import FeedbackImportContractTest
from validate import validate_repository


class HandoffE2EContractTest(FeedbackImportContractTest):
    def _bundle_files(self, output: Path) -> dict[str, bytes]:
        manifest = yaml.safe_load((output / "manifest.yaml").read_text(encoding="utf-8"))
        files: dict[str, bytes] = {}
        for entry in manifest["files"]:
            relative = entry["path"]
            self.assertFalse(relative.startswith("/"))
            self.assertNotIn("..", Path(relative).parts)
            path = output / relative
            self.assertTrue(path.is_file())
            raw = path.read_bytes()
            self.assertEqual(entry["size_bytes"], len(raw))
            self.assertEqual(entry["sha256"], f"sha256:{hashlib.sha256(raw).hexdigest()}")
            files[relative] = raw
        file_set = canonical_sha256(
            [
                {"path": entry["path"], "size_bytes": entry["size_bytes"], "sha256": entry["sha256"]}
                for entry in manifest["files"]
            ]
        )
        self.assertEqual(file_set, manifest["integrity"]["file_set_sha256"])
        return files

    def test_handoff_bundle_is_self_contained_and_tamper_evident(self) -> None:
        root, project = self.make_project()
        output = root / "data" / "handoffs" / "feedback-import"
        export_handoff(root, "project/feedback-import", output, allow_dirty=True)
        files = self._bundle_files(output)
        self.assertIn("production-handoff.yaml", files)
        self.assertIn("schemas/production-handoff.schema.json", files)
        source_index = files["artifacts/source-ref-index.yaml"]
        self.assertNotIn(b"source_location", source_index)
        self.assertNotIn(b"PRIVATE_RAW", source_index)
        self.assertNotIn(b"RESTRICTED", source_index)
        self.assertFalse(any(b"/Users/" in raw or b"/private/" in raw for raw in files.values()))

        external_copy = root / "external-consumer" / "handoff"
        shutil.copytree(output, external_copy)
        self.assertEqual(files, {path.relative_to(external_copy).as_posix(): path.read_bytes() for path in external_copy.rglob("*") if path.is_file() and path.name != "manifest.yaml"})

        (output / "artifacts" / "creative-direction.md").write_text("tampered", encoding="utf-8")
        with self.assertRaises(AssertionError):
            self._bundle_files(output)

    def test_pass_fail_deviation_and_critical_results_round_trip(self) -> None:
        scenarios = ("pass", "fail", "deviation", "critical")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                root, project = self.make_project()
                self.configure_result_schema(root)
                result = self.make_result(project, result_id=f"PR{scenarios.index(scenario) + 1:03d}")
                if scenario == "fail":
                    result["test_results"][0]["result"] = "FAIL"
                elif scenario == "deviation":
                    result["deviations"] = [{"id": "DEV001", "statement": "The spacing was adjusted during the fixture run."}]
                elif scenario == "critical":
                    result["incidents"] = [{"id": "INC001", "severity": "CRITICAL", "statement": "Synthetic safety incident."}]
                result["integrity"] = {"content_sha256": result_sha256(result)}
                result_path = self.write_result(root, result, f"{scenario}.json")
                summary = import_production_result(root, result_path, dry_run=True)
                expected_level = {"pass": "NONE", "fail": "MAJOR", "deviation": "MAJOR", "critical": "CRITICAL"}[scenario]
                self.assertEqual(expected_level, summary["impact_level"])
                applied = import_production_result(root, result_path, apply=True)
                self.assertEqual("APPLIED", applied["status"])
                self.assertEqual([], validate_repository(root, "project/feedback-import"))
                if scenario == "critical":
                    self.assertTrue(applied["human_approval_required"])

    def test_schema_mismatch_and_corrupt_feedback_fail_closed(self) -> None:
        root, project = self.make_project()
        self.configure_result_schema(root)
        result = self.make_result(project)
        result["schema_version"] = "2.0.0"
        result["integrity"] = {"content_sha256": result_sha256(result)}
        mismatch_path = self.write_result(root, result, "schema-mismatch.json")
        with self.assertRaisesRegex(ResultImportError, "EXTERNAL-SCHEMA"):
            import_production_result(root, mismatch_path, dry_run=True)

        valid = self.make_result(project, result_id="PR002")
        valid_path = self.write_result(root, valid, "valid.json")
        import_production_result(root, valid_path, apply=True)
        feedback_path = project / "07_runtime/production-feedback-imports.jsonl"
        with feedback_path.open("a", encoding="utf-8") as handle:
            handle.write("{broken-json\n")
        findings = validate_repository(root, "project/feedback-import")
        self.assertTrue(any("JSONL" in finding.rule for finding in findings))

    def test_research_only_fixture_remains_backward_compatible(self) -> None:
        root = self.make_root()
        project = run_offline_fixture(root, "harmony-study", REPO_ROOT / "tests" / "fixtures" / "harmony")

        self.assertEqual([], validate_repository(root, "project/harmony-study"))
        manifest = yaml.safe_load((project / "manifest.yaml").read_text(encoding="utf-8"))
        self.assertNotEqual("PRODUCTION_HANDOFF", manifest.get("workflow_mode"))
        self.assertNotIn("production_handoff", manifest["entry_points"])


if __name__ == "__main__":
    unittest.main()
