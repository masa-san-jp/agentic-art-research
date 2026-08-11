from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from canonical import canonical_sha256
from build_graph import build_graph
from build_handoff import build_handoff
from export_handoff import export_handoff
from import_production_result import ResultImportError, import_production_result, result_sha256
from impact import impact_report
from run_project import run_offline_fixture
from validate import validate_repository

try:
    from .test_feedback_import import FeedbackImportContractTest as _FeedbackImportContractTest
except ImportError:
    from test_feedback_import import FeedbackImportContractTest as _FeedbackImportContractTest


class HandoffE2EContractTest(_FeedbackImportContractTest):
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
        scenario = yaml.safe_load((REPO_ROOT / "tests" / "fixtures" / "harmony-handoff" / "scenario.yaml").read_text(encoding="utf-8"))
        root, project = self.make_project()
        output = root / "data" / "handoffs" / scenario["scenario"]
        export_handoff(root, scenario["research_project_id"], output, allow_dirty=True)
        files = self._bundle_files(output)
        for relative in scenario["handoff"]["expected_bundle_paths"]:
            if relative == "manifest.yaml":
                self.assertTrue((output / relative).is_file())
            else:
                self.assertIn(relative, files)
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

    def test_actual_production_schema_dry_run_apply_and_idempotency(self) -> None:
        root, project = self.make_project()
        schema_path = root / "schemas" / "external" / "production-result.v1.schema.json"
        shutil.copyfile(REPO_ROOT / "schemas" / "external" / "production-result.v1.schema.json", schema_path)
        self.assertTrue(schema_path.is_file())
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        result = self.make_result(project)
        errors = list(Draft202012Validator(schema).iter_errors(result))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))
        result_path = self.write_result(root, result, "actual-production-result.json")
        tracked = [
            project / "02_evidence" / "evidence-ledger.jsonl",
            project / "06_governance" / "production-change-requests.yaml",
            project / "07_runtime" / "production-feedback-imports.jsonl",
            project / "07_runtime" / "run-log.jsonl",
            project / "manifest.yaml",
            project / "07_runtime" / "research-state.json",
        ]
        before = {path: path.read_bytes() for path in tracked}

        preview = import_production_result(root, result_path, dry_run=True)
        self.assertEqual("DRY_RUN", preview["status"])
        self.assertEqual("fb15f32bf1eef0155c853c4b7c4b94df6b1bd78b", preview["schema_snapshot"]["source_commit"])
        self.assertEqual(before, {path: path.read_bytes() for path in tracked})

        applied = import_production_result(root, result_path, apply=True)
        self.assertEqual("APPLIED", applied["status"])
        repeated_before = {path: path.read_bytes() for path in tracked}
        repeated = import_production_result(root, result_path, apply=True)
        self.assertEqual("ALREADY_APPLIED", repeated["status"])
        self.assertEqual(repeated_before, {path: path.read_bytes() for path in tracked})

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

    def test_fixed_handoff_scenario_round_trips_to_result_graph_and_impact(self) -> None:
        scenario = yaml.safe_load((REPO_ROOT / "tests" / "fixtures" / "harmony-handoff" / "scenario.yaml").read_text(encoding="utf-8"))
        root, project = self.make_project()
        handoff_path = build_handoff(root, scenario["research_project_id"], generated_at=self.generated_at, research_commit=self.commit)
        bundle_path = root / "data" / "handoffs" / scenario["scenario"]
        export_handoff(root, scenario["research_project_id"], bundle_path, allow_dirty=True)
        self.assertEqual(scenario["handoff"]["id"], yaml.safe_load(handoff_path.read_text(encoding="utf-8"))["handoff_id"])
        for relative in scenario["handoff"]["expected_bundle_paths"]:
            self.assertTrue((bundle_path / relative).is_file(), relative)

        self.configure_result_schema(root)
        result = self.make_result(project, result_id=scenario["feedback"]["result_id"])
        result_path = self.write_result(root, result)
        self.assertEqual(scenario["feedback"]["schema_version"], result["schema_version"])
        summary = import_production_result(root, result_path, apply=True)
        self.assertEqual("APPLIED", summary["status"])

        graph = build_graph(root)
        project_id = scenario["research_project_id"]
        edges = {(edge["from"], edge["to"], edge["type"]) for edge in graph["edges"]}
        expected_types = set(scenario["feedback"]["expected_graph_edges"])
        observed_types = {
            edge[2]
            for edge in edges
            if edge[0] == f"{project_id}::HO001"
            or edge[0] == f"{project_id}::PR001"
            or edge[0] == f"{project_id}::PR001/OB001"
        }
        self.assertTrue(expected_types.issubset(observed_types))
        report = impact_report(graph, "PR001")
        self.assertTrue(report["found"])
        downstream_ids = {item["id"] for item in report["downstream"]}
        self.assertIn("PR001/OB001", downstream_ids)
        self.assertIn("EV003", downstream_ids)

    def test_fixed_handoff_faults_are_named_and_corrupt_feedback_fails_closed(self) -> None:
        root, project = self.make_project()
        self.configure_result_schema(root)

        unsupported = self.make_result(project)
        unsupported["schema_version"] = "9.9.9"
        unsupported["integrity"] = {"content_sha256": result_sha256(unsupported)}
        unsupported_path = self.write_result(root, unsupported, "unsupported.json")
        with self.assertRaisesRegex(ResultImportError, "EXTERNAL-SCHEMA"):
            import_production_result(root, unsupported_path, dry_run=True)

        tampered = self.make_result(project, result_id="PR002")
        tampered["integrity"] = {"content_sha256": "sha256:" + "0" * 64}
        tampered_path = self.write_result(root, tampered, "tampered.json")
        with self.assertRaisesRegex(ResultImportError, "FEEDBACK-HASH"):
            import_production_result(root, tampered_path, dry_run=True)

        valid_path = self.write_result(root, self.make_result(project, result_id="PR003"), "valid.json")
        import_production_result(root, valid_path, apply=True)
        feedback_path = project / "07_runtime" / "production-feedback-imports.jsonl"
        with feedback_path.open("a", encoding="utf-8") as handle:
            handle.write("{broken-json\n")
        with self.assertRaises(ResultImportError) as context:
            import_production_result(root, valid_path, dry_run=True)
        self.assertIn("FEEDBACK-RESEARCH-VALIDATION", str(context.exception))
        self.assertIn("JSONL", str(context.exception))

    def test_research_only_fixture_remains_backward_compatible(self) -> None:
        root = self.make_root()
        project = run_offline_fixture(root, "harmony-study", REPO_ROOT / "tests" / "fixtures" / "harmony")

        self.assertEqual([], validate_repository(root, "project/harmony-study"))
        manifest = yaml.safe_load((project / "manifest.yaml").read_text(encoding="utf-8"))
        self.assertNotEqual("PRODUCTION_HANDOFF", manifest.get("workflow_mode"))
        self.assertNotIn("production_handoff", manifest["entry_points"])


del _FeedbackImportContractTest


if __name__ == "__main__":
    unittest.main()
