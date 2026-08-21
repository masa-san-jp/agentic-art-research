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

from build_handoff import build_handoff
from complete import complete_project, evaluate_project
from new_project import create_project
from test_schemas import validator_for
from validate import validate_repository


def _set_status(project, status: str) -> None:
    manifest_path = project / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["project"]["status"] = status
    manifest["project"]["updated_at"] = "2026-08-11T00:00:00+09:00"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    state_path = project / "07_runtime" / "research-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = status
    state["updated_at"] = "2026-08-11T00:00:00+09:00"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


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
        # This fixture proves the shape of the report, not a piece of research,
        # so it lowers the volume floor and says why. An override without a
        # reason is refused, which the volume tests below cover.
        plan_path = project / "01_planning" / "research-plan.yaml"
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan["minimums"] = {
            "reason": "Contract fixture for the completion report; it carries no research records.",
            "evidence": 0, "claims": 0, "insights": 0, "decisions": 0, "requirements": 0,
            "rejected_options": 0, "uncertainties": 0, "prior_art": 0, "self_repetition_review": 0,
        }
        plan_path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")
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

    def test_a_project_below_the_volume_floor_is_incomplete_not_gapped(self) -> None:
        """"調べたうえで埋まらなかった" と "調べていない" は別物なので、同じ語で報告しない。"""
        root = self.make_root()
        project = create_project(
            root, "thin-project", "Thin", "creator/fixture", created_at="2026-08-11T00:00:00+09:00")
        _set_status(project, "VALIDATING")

        report = evaluate_project(root, "project/thin-project")

        self.assertEqual("INCOMPLETE", report["status"])
        self.assertIn("evidence", report["volume"]["shortfall"])

    def test_lowering_the_floor_without_a_reason_does_not_lower_it(self) -> None:
        root = self.make_root()
        project = create_project(
            root, "silent-override", "Silent", "creator/fixture", created_at="2026-08-11T00:00:00+09:00")
        _set_status(project, "VALIDATING")
        plan_path = project / "01_planning" / "research-plan.yaml"
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan["minimums"] = {"evidence": 0}
        plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")

        report = evaluate_project(root, "project/silent-override")

        self.assertIn("evidence", report["volume"]["unexplained_overrides"])

    def test_a_handoff_is_refused_while_the_research_is_incomplete(self) -> None:
        root = self.make_root()
        project = create_project(
            root, "no-handoff", "No handoff", "creator/fixture", created_at="2026-08-11T00:00:00+09:00")
        _set_status(project, "VALIDATING")
        manifest_path = project / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["workflow_mode"] = "PRODUCTION_HANDOFF"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        report = evaluate_project(root, "project/no-handoff")
        (project / "07_runtime" / "completion-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        with self.assertRaises(Exception) as caught:
            build_handoff(root, "projects/no-handoff", generated_at="2026-08-11T00:30:00+09:00",
                          research_commit="0" * 40, handoff_id="HO001", revision=1)

        self.assertIn("INCOMPLETE", str(caught.exception))

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
