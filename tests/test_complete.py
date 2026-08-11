from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from complete import complete_project
from new_project import create_project
from test_schemas import validator_for
from validate import validate_repository


class CompletionContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def prepare_validating_project(self, *, governance_complete: bool) -> Path:
        root = self.make_root()
        project = create_project(
            root,
            "completion-test",
            "Completion Test",
            "creator/fixture",
            created_at="2026-08-11T00:00:00+09:00",
        )
        manifest_path = project / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["project"]["status"] = "VALIDATING"
        manifest["project"]["updated_at"] = "2026-08-11T00:00:00+09:00"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        state_path = project / "07_runtime" / "research-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "VALIDATING"
        state["updated_at"] = "2026-08-11T00:00:00+09:00"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        if governance_complete:
            (project / "06_governance" / "rights-register.yaml").write_text(
                "rights:\n  - id: RT001\n    status: CLEARED\n", encoding="utf-8"
            )
            (project / "06_governance" / "privacy-review.yaml").write_text(
                "status: COMPLETE\nreviewed_at: '2026-08-11T00:00:00+09:00'\nprivate_raw_in_git: false\nfindings: []\n",
                encoding="utf-8",
            )
        return root

    def assert_report_schema_valid(self, report: dict) -> None:
        errors = list(validator_for("completion-report").iter_errors(report))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_complete_report_is_schema_valid_and_reproducible(self) -> None:
        root_a = self.prepare_validating_project(governance_complete=True)
        root_b = self.prepare_validating_project(governance_complete=True)
        report_a = complete_project(root_a, "project/completion-test", completed_at="2026-08-11T00:30:00+09:00")
        report_b = complete_project(root_b, "project/completion-test", completed_at="2026-08-11T00:30:00+09:00")
        self.assertEqual("COMPLETE", report_a["status"])
        self.assertEqual(report_a, report_b)
        self.assert_report_schema_valid(report_a)
        self.assertEqual([], validate_repository(root_a))
        manifest = yaml.safe_load((root_a / "projects/completion-test/manifest.yaml").read_text(encoding="utf-8"))
        self.assertEqual("COMPLETE", manifest["project"]["status"])

    def test_complete_with_gaps_report_is_schema_valid_and_idempotent(self) -> None:
        root = self.prepare_validating_project(governance_complete=False)
        report = complete_project(root, "project/completion-test", completed_at="2026-08-11T00:30:00+09:00")
        repeated = complete_project(root, "project/completion-test", completed_at="2026-08-11T00:30:00+09:00")
        self.assertEqual("COMPLETE_WITH_GAPS", report["status"])
        self.assertEqual(report, repeated)
        self.assert_report_schema_valid(report)
        self.assertGreaterEqual(len(report["gaps"]), 1)
        self.assertEqual([], validate_repository(root))


if __name__ == "__main__":
    unittest.main()
