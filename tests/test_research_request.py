from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from accept_research_request import RequestAcceptanceError, accept_research_request
from new_project import create_project
from validate import validate_repository


class ResearchRequestAcceptanceTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def copy_fixture(self, root: Path, name: str = "research-request.yaml") -> Path:
        path = root / "incoming" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / "tests" / "fixtures" / "schema-valid" / name, path)
        return path

    def test_dry_run_then_apply_materializes_valid_research_only_project(self) -> None:
        root = self.make_root()
        request = self.copy_fixture(root)

        preview = accept_research_request(root, request, dry_run=True)
        self.assertEqual("DRY_RUN", preview["status"])
        self.assertFalse((root / "projects" / "harmony-study").exists())

        applied = accept_research_request(
            root,
            request,
            apply=True,
            accepted_at="2026-08-12T09:05:00+09:00",
        )
        self.assertEqual("APPLIED", applied["status"])
        project = root / "projects" / "harmony-study"
        manifest = yaml.safe_load((project / "manifest.yaml").read_text(encoding="utf-8"))
        self.assertEqual("RESEARCH_ONLY", manifest["workflow_mode"])
        self.assertEqual("00_intake/research-request.yaml", manifest["entry_points"]["research_request"])
        self.assertTrue((project / "00_intake" / "research-request-receipt.yaml").is_file())
        self.assertEqual([], validate_repository(root), "accepted project must pass the repository validator")

    def test_reapply_same_request_is_idempotent(self) -> None:
        root = self.make_root()
        request = self.copy_fixture(root)
        accept_research_request(root, request, apply=True, accepted_at="2026-08-12T09:05:00+09:00")
        project = root / "projects" / "harmony-study"
        before = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}

        repeated = accept_research_request(root, request, apply=True, accepted_at="2026-08-12T10:05:00+09:00")

        self.assertEqual("ALREADY_APPLIED", repeated["status"])
        after = {path.relative_to(project): path.read_bytes() for path in project.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_same_request_id_with_changed_content_is_rejected(self) -> None:
        root = self.make_root()
        request = self.copy_fixture(root)
        accept_research_request(root, request, apply=True, accepted_at="2026-08-12T09:05:00+09:00")
        changed = root / "incoming" / "changed.yaml"
        value = yaml.safe_load(request.read_text(encoding="utf-8"))
        value["intent"]["purpose"] = "Changed content must not replace an accepted request."
        changed.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(RequestAcceptanceError, "REQUEST-CONFLICT"):
            accept_research_request(root, changed, dry_run=True)

    def test_existing_project_is_not_overwritten(self) -> None:
        root = self.make_root()
        create_project(root, "harmony-study", "Existing Project")
        request = self.copy_fixture(root)

        with self.assertRaisesRegex(RequestAcceptanceError, "PROJECT-CONFLICT"):
            accept_research_request(root, request, apply=True)

    def test_schema_and_security_fail_closed(self) -> None:
        root = self.make_root()
        invalid = root / "incoming" / "invalid.yaml"
        invalid.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            REPO_ROOT / "tests" / "fixtures" / "schema-invalid" / "research-request-unknown-field.yaml",
            invalid,
        )
        with self.assertRaisesRegex(RequestAcceptanceError, "SCHEMA:additionalProperties"):
            accept_research_request(root, invalid, dry_run=True)

        request = self.copy_fixture(root)
        value = yaml.safe_load(request.read_text(encoding="utf-8"))
        value["source"]["artifact_uri"] = "file:///Users/example/private.txt"
        request.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(RequestAcceptanceError, "REQUEST-SECURITY"):
            accept_research_request(root, request, dry_run=True)

    def test_empty_plan_is_rejected_before_acceptance(self) -> None:
        root = self.make_root()
        request = self.copy_fixture(root)
        plan_path = root / "templates" / "project" / "01_planning" / "research-plan.yaml"
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan["tasks"] = []
        plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(RequestAcceptanceError, "PLAN-WITHOUT-TASKS"):
            accept_research_request(root, request, apply=True, accepted_at="2026-08-12T09:05:00+09:00")

        self.assertFalse((root / "projects" / "harmony-study").exists())

    def test_external_work_root_initializes_runtime_from_protocol_root(self) -> None:
        """A generated work root must not need a second copy of protocol policy."""
        protocol_root = self.make_root()
        work_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work_root, True)
        (work_root / "projects").mkdir()
        (work_root / "data").mkdir()
        request = work_root / "incoming" / "research-request.yaml"
        request.parent.mkdir(parents=True)
        shutil.copyfile(REPO_ROOT / "tests" / "fixtures" / "schema-valid" / "research-request.yaml", request)

        applied = accept_research_request(
            work_root,
            request,
            apply=True,
            accepted_at="2026-08-12T09:05:00+09:00",
            protocol_root=protocol_root,
        )

        self.assertEqual("APPLIED", applied["status"])
        project = work_root / "projects" / "harmony-study"
        state = yaml.safe_load((project / "07_runtime" / "research-state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["task_runtime"]["tasks"])
        self.assertEqual([], validate_repository(work_root, "project/harmony-study", protocol_root=protocol_root))
        self.assertFalse(list(protocol_root.glob("projects/*")))


if __name__ == "__main__":
    unittest.main()


class AcceptedProjectIsReadyToWorkTest(ResearchRequestAcceptanceTest):
    """An accepted project has a runtime ready for the first orchestration step."""

    def test_accepted_project_can_claim_its_first_task(self) -> None:
        root = self.make_root()
        request = self.copy_fixture(root)

        accept_research_request(root, request, apply=True, accepted_at="2026-08-12T09:05:00+09:00")

        import json

        from task_runtime import claim_next

        state = json.loads((root / "projects" / "harmony-study" / "07_runtime" / "research-state.json").read_text(encoding="utf-8"))
        self.assertIn("task_runtime", state)
        self.assertTrue(state["task_runtime"]["tasks"])

        claimed = claim_next(root, "project/harmony-study", "tester", now="2026-08-12T09:10:00+09:00")
        self.assertTrue(claimed["task_id"])

    def test_reaccepting_does_not_disturb_the_runtime(self) -> None:
        root = self.make_root()
        request = self.copy_fixture(root)
        accept_research_request(root, request, apply=True, accepted_at="2026-08-12T09:05:00+09:00")
        state_path = root / "projects" / "harmony-study" / "07_runtime" / "research-state.json"
        before = state_path.read_bytes()

        accept_research_request(root, request, apply=True, accepted_at="2026-08-12T10:05:00+09:00")

        self.assertEqual(before, state_path.read_bytes())
