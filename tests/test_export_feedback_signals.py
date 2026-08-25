from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from export_feedback_signals import FeedbackSignalExportError, export_feedback_signals
from import_production_result import result_sha256
import test_feedback_import as feedback_import_tests
from _common import load_json


class FeedbackSignalExportContractTest(unittest.TestCase):
    def make_imported_project(self) -> tuple[Path, Path, feedback_import_tests.FeedbackImportContractTest]:
        helper = feedback_import_tests.FeedbackImportContractTest()
        root, project = helper.make_project()
        helper.configure_result_schema(root)
        result_path = helper.write_result(root, helper.make_result(project))
        from import_production_result import import_production_result

        import_production_result(root, result_path, apply=True)
        self.addCleanup(helper.doCleanups)
        return root, project, helper

    def rewrite_imported_result(self, project: Path, mutate) -> None:
        feedback_path = project / "07_runtime" / "production-feedback-imports.jsonl"
        result = json.loads(next(line for line in feedback_path.read_text(encoding="utf-8").splitlines() if line.strip()))
        mutate(result)
        result["integrity"] = {"content_sha256": result_sha256(result)}
        feedback_path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
        run_log_path = project / "07_runtime" / "run-log.jsonl"
        events = [json.loads(line) for line in run_log_path.read_text(encoding="utf-8").splitlines() if line]
        for event in events:
            if event.get("event_type") == "PRODUCTION_FEEDBACK_IMPORTED":
                event["result_sha256"] = result_sha256(result)
        run_log_path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n", encoding="utf-8")

    def test_export_is_schema_valid_deterministic_and_read_only(self) -> None:
        root, project, _ = self.make_imported_project()
        target = "project/feedback-import"
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in project.rglob("*")
            if path.is_file()
        }
        output = root / "exports" / "PR001"

        summary = export_feedback_signals(root, target, "PR001", output)

        self.assertEqual("EXPORTED", summary["status"])
        self.assertEqual({"manifest.json", "signals.jsonl"}, {path.name for path in output.iterdir()})
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        signals = [json.loads(line) for line in (output / "signals.jsonl").read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual("RSE-feedback-import-PR001", manifest["export_id"])
        self.assertEqual(1, manifest["signal_count"])
        self.assertEqual("AT001", signals[0]["acceptance_test_id"])
        self.assertEqual(["RQ001"], signals[0]["requirement_ids"])
        self.assertIsNone(signals[0]["presentation_conditions"])
        self.assertEqual(["OB001"], [observation["id"] for observation in signals[0]["observations"]])

        schema = load_json(REPO_ROOT / "schemas" / "research-signal-export.schema.json")
        manifest_errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
        signal_schema = dict(schema["$defs"]["signal"])
        signal_schema["$defs"] = schema["$defs"]
        signal_errors = list(Draft202012Validator(signal_schema, format_checker=FormatChecker()).iter_errors(signals[0]))
        self.assertEqual([], manifest_errors)
        self.assertEqual([], signal_errors)
        self.assertEqual(before, {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in project.rglob("*")
            if path.is_file()
        })

    def test_same_input_in_different_roots_has_identical_bundle_bytes(self) -> None:
        root_a, _, _ = self.make_imported_project()
        output_a = root_a / "exports" / "PR001"
        export_feedback_signals(root_a, "project/feedback-import", "PR001", output_a)
        bytes_a = {name: (output_a / name).read_bytes() for name in ("manifest.json", "signals.jsonl")}

        root_b, _, _ = self.make_imported_project()
        output_b = root_b / "exports" / "PR001"
        export_feedback_signals(root_b, "project/feedback-import", "PR001", output_b)
        bytes_b = {name: (output_b / name).read_bytes() for name in ("manifest.json", "signals.jsonl")}

        self.assertEqual(bytes_a, bytes_b)

    def test_same_output_is_idempotent_and_conflict_is_non_destructive(self) -> None:
        root, _, _ = self.make_imported_project()
        output = root / "exports" / "PR001"
        first = export_feedback_signals(root, "project/feedback-import", "PR001", output)
        before = {name: (output / name).read_bytes() for name in ("manifest.json", "signals.jsonl")}
        repeated = export_feedback_signals(root, "project/feedback-import", "PR001", output)
        self.assertEqual("EXPORTED", first["status"])
        self.assertEqual("ALREADY_EXPORTED", repeated["status"])

        (output / "signals.jsonl").write_text("conflict\n", encoding="utf-8")
        with self.assertRaisesRegex(FeedbackSignalExportError, "FEEDBACK-EXPORT-CONFLICT"):
            export_feedback_signals(root, "project/feedback-import", "PR001", output)
        self.assertEqual(b"conflict\n", (output / "signals.jsonl").read_bytes())
        self.assertNotEqual(before["signals.jsonl"], (output / "signals.jsonl").read_bytes())

    def test_unimported_result_and_hash_mismatch_fail_closed(self) -> None:
        helper = feedback_import_tests.FeedbackImportContractTest()
        root, project = helper.make_project()
        self.addCleanup(helper.doCleanups)
        with self.assertRaisesRegex(FeedbackSignalExportError, "FEEDBACK-EXPORT-NOT-IMPORTED"):
            export_feedback_signals(root, "project/feedback-import", "PR001", root / "exports" / "PR001")

        helper.configure_result_schema(root)
        result_path = helper.write_result(root, helper.make_result(project))
        from import_production_result import import_production_result

        import_production_result(root, result_path, apply=True)
        run_log_path = project / "07_runtime" / "run-log.jsonl"
        events = [json.loads(line) for line in run_log_path.read_text(encoding="utf-8").splitlines() if line]
        next(event for event in events if event.get("event_type") == "PRODUCTION_FEEDBACK_IMPORTED")["result_sha256"] = "sha256:" + "0" * 64
        run_log_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(FeedbackSignalExportError, "FEEDBACK-EXPORT-HASH"):
            export_feedback_signals(root, "project/feedback-import", "PR001", root / "exports" / "PR001")

    def test_unknown_field_and_private_free_text_are_rejected_without_bundle(self) -> None:
        for mutation, expected in (
            (lambda result: result.update({"respondent_name": "Alice"}), "FEEDBACK-EXPORT-CONTRACT"),
            (lambda result: result["observations"][0].update({"statement": "Contact alice@example.com"}), "FEEDBACK-EXPORT-PRIVACY"),
        ):
            with self.subTest(expected=expected):
                root, project, _ = self.make_imported_project()
                self.rewrite_imported_result(project, mutation)
                output = root / "exports" / "PR001"
                with self.assertRaisesRegex(FeedbackSignalExportError, expected):
                    export_feedback_signals(root, "project/feedback-import", "PR001", output)
                self.assertFalse(output.exists())

    def test_unresolved_acceptance_and_requirement_are_named_references(self) -> None:
        root, project, _ = self.make_imported_project()
        self.rewrite_imported_result(project, lambda result: result["test_results"][0].update({"acceptance_test_id": "AT999"}))
        with self.assertRaisesRegex(FeedbackSignalExportError, "FEEDBACK-EXPORT-REFERENCE"):
            export_feedback_signals(root, "project/feedback-import", "PR001", root / "exports" / "AT999")

        root, project, _ = self.make_imported_project()
        acceptance_path = project / "05_production" / "acceptance-tests.yaml"
        acceptance_path.write_text(
            acceptance_path.read_text(encoding="utf-8").replace("target_requirement: RQ001", "target_requirement: RQ999"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(FeedbackSignalExportError, "FEEDBACK-EXPORT-REFERENCE"):
            export_feedback_signals(root, "project/feedback-import", "PR001", root / "exports" / "RQ999")

    def test_canonical_projects_and_data_are_not_export_targets(self) -> None:
        root, _, _ = self.make_imported_project()
        with self.assertRaisesRegex(FeedbackSignalExportError, "FEEDBACK-EXPORT-BOUNDARY"):
            export_feedback_signals(root, "project/feedback-import", "PR001", root / "data" / "exports")


if __name__ == "__main__":
    unittest.main()
