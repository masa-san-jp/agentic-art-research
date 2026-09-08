from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(REPO_ROOT / "tools"))

from attempt_workspace import (  # noqa: E402
    AttemptWorkspaceError,
    build_changeset,
    create_attempt_workspace,
    inspect_attempt,
    load_write_targets,
    promote_attempt,
    quarantine_attempt,
    recover_quarantined_attempt,
    snapshot_project,
)
from _common import load_yaml  # noqa: E402
from new_project import create_project  # noqa: E402


class AttemptWorkspaceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.output.mkdir()
        (self.work / "data").mkdir()
        (self.work / "projects").mkdir()
        self.project = create_project(self.work, "probe", "Probe", protocol_root=REPO_ROOT)
        self.addCleanup(shutil.rmtree, self.root, True)

    def make_attempt(self, role: str = "planner", attempt_id: str = "AT001"):
        return create_attempt_workspace(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id=attempt_id,
            role=role,
        )

    def test_snapshot_is_schema_ready_and_workspace_isolated_and_idempotent(self) -> None:
        before = sorted(path.relative_to(self.project).as_posix() for path in self.project.rglob("*"))
        attempt = self.make_attempt()
        self.assertNotEqual(self.project, attempt.project)
        self.assertTrue(attempt.baseline_manifest.is_file())
        self.assertEqual(before, sorted(path.relative_to(attempt.project).as_posix() for path in attempt.project.rglob("*")))
        first = attempt.baseline_manifest.read_bytes()
        repeated = self.make_attempt()
        self.assertEqual(attempt.root, repeated.root)
        self.assertEqual(first, repeated.baseline_manifest.read_bytes())
        self.assertEqual(before, sorted(path.relative_to(self.project).as_posix() for path in self.project.rglob("*")))

    def test_declared_write_is_deterministic_and_promotes_only_after_validation(self) -> None:
        attempt = self.make_attempt()
        target = attempt.project / "01_planning/question-register.yaml"
        target.write_text(target.read_text(encoding="utf-8") + "\n# deterministic attempt edit\n", encoding="utf-8")
        first = inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        second = inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        self.assertEqual(first, second)
        self.assertEqual(first["changes"][0]["operation"], "MODIFY")
        self.assertNotIn("01_planning/question-register.yaml", (self.project / "01_planning/question-register.yaml").read_text())
        promoted = promote_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        self.assertEqual(first, promoted)
        self.assertIn("deterministic attempt edit", (self.project / "01_planning/question-register.yaml").read_text())

    def test_any_unauthorized_change_rejects_the_whole_attempt(self) -> None:
        attempt = self.make_attempt()
        allowed = attempt.project / "01_planning/question-register.yaml"
        allowed.write_text(allowed.read_text(encoding="utf-8") + "\n# allowed candidate\n", encoding="utf-8")
        (attempt.project / "README.md").write_text("forbidden project change\n", encoding="utf-8")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-WRITE-BOUNDARY"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        self.assertNotIn("allowed candidate", (self.project / "01_planning/question-register.yaml").read_text())

    def test_translator_can_author_required_brief_categories_and_final_comparison(self) -> None:
        attempt = self.make_attempt(role="production-translator")
        for relative in ("05_production/production-brief.yaml", "05_production/reference-categories.yaml"):
            target = attempt.project / relative
            target.write_text("categories: {}\n" if "categories" in relative else target.read_text() + "\n# authored proposal\n")
        report = inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        self.assertEqual({"05_production/production-brief.yaml", "05_production/reference-categories.yaml"}, {row["path"] for row in report["changes"]})
        forbidden = self.make_attempt(role="collector", attempt_id="AT-FORBIDDEN")
        (forbidden.project / "05_production/production-brief.yaml").write_text("forbidden\n")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-WRITE-BOUNDARY"):
            inspect_attempt(forbidden, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

    def test_cumulative_local_references_do_not_exempt_prose_or_symlinks(self) -> None:
        attempt = self.make_attempt(role="production-translator")
        payload = self.root / "curated-payload.json"
        payload.write_text("{}\n")
        store = self.root / "owner-store"
        store.mkdir()
        document = {"contract_version":"cumulative-specificity-request/v1", "rule_version":"cumulative-specificity-policy/v1", "creator_id":"test", "origin_instance_id":"instance", "at":"2026-09-08T00:00:00Z", "seed":1, "project_snapshot":{}, "inputs":[{"payload_path":str(payload)}], "sources":[], "candidates":[{}], "memory_query":{"store_root":str(store)}}
        target = attempt.project / "04_decisions/cumulative-specificity-request.json"
        target.write_text(json.dumps(document))
        inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        for field, value in (("creator_id", "/private/secret"), ("creator_id", "PRIVATE_RAW")):
            changed = copy.deepcopy(document)
            changed[field] = value
            target.write_text(json.dumps(changed))
            with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-SECURITY"):
                inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        alias = self.root / "alias.json"
        alias.symlink_to(payload)
        document["inputs"][0]["payload_path"] = str(alias)
        target.write_text(json.dumps(document))
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-SECURITY"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        document["inputs"][0]["payload_path"] = str(self.root / "missing.json")
        target.write_text(json.dumps(document))
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-SECURITY"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

    def test_runtime_namespace_is_not_a_worker_write_target(self) -> None:
        targets = load_write_targets(REPO_ROOT, "collector")
        self.assertNotIn("07_runtime/run-log.jsonl", {target["path"] for target in targets})
        collector = (load_yaml(REPO_ROOT / "config/task-roles.yaml") or {})["roles"]["collector"]
        self.assertIn("07_runtime/run-log.jsonl", {target["path"] for target in collector["runtime_targets"]})
        attempt = self.make_attempt(role="collector")
        runtime = attempt.project / "07_runtime/run-log.jsonl"
        runtime.write_text(runtime.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-WRITE-BOUNDARY"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

    def test_protected_roots_symlink_hardlink_collision_size_and_path_are_rejected(self) -> None:
        attempt = self.make_attempt(attempt_id="AT002")
        (attempt.project / "README.md").unlink()
        (attempt.project / "README.md").symlink_to("01_planning/research-plan.yaml")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-(UNSAFE-FILE|WRITE-BOUNDARY)"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

        attempt = self.make_attempt(attempt_id="AT003")
        os.link(attempt.project / "README.md", attempt.project / "01_planning/hardlink.md")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-UNSAFE-FILE"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

        attempt = self.make_attempt(attempt_id="AT004")
        (attempt.project / "01_planning/Foo").write_text("x", encoding="utf-8")
        (attempt.project / "01_planning/foo").write_text("y", encoding="utf-8")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-(UNSAFE-FILE|WRITE-BOUNDARY)"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

        attempt = self.make_attempt(attempt_id="AT005")
        (attempt.project / "01_planning/large").write_bytes(b"x" * (20_971_521))
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-UNSAFE-FILE"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

    def test_secret_private_forbidden_and_protected_root_mutations_fail_closed(self) -> None:
        attempt = self.make_attempt(attempt_id="AT006")
        target = attempt.project / "01_planning/question-register.yaml"
        target.write_text(target.read_text(encoding="utf-8") + "\nPRIVATE_RAW\n", encoding="utf-8")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-SECURITY"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

        attempt = self.make_attempt(attempt_id="AT007")
        (self.work / "data/unauthorized.txt").write_text("outside\n", encoding="utf-8")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-WRITE-BOUNDARY"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

        attempt = self.make_attempt(attempt_id="AT008")
        (self.output / "unauthorized.txt").write_text("outside\n", encoding="utf-8")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-WRITE-BOUNDARY"):
            inspect_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)

    def test_baseline_conflict_does_not_overwrite_concurrent_project_change(self) -> None:
        attempt = self.make_attempt(attempt_id="AT009")
        candidate = attempt.project / "01_planning/question-register.yaml"
        candidate.write_text(candidate.read_text(encoding="utf-8") + "\n# candidate\n", encoding="utf-8")
        canonical = self.project / "01_planning/question-register.yaml"
        canonical.write_text(canonical.read_text(encoding="utf-8") + "\n# concurrent\n", encoding="utf-8")
        with self.assertRaisesRegex(AttemptWorkspaceError, "ATTEMPT-BASELINE-CONFLICT"):
            promote_attempt(attempt, protocol_root=REPO_ROOT, work_root=self.work, output_root=self.output)
        self.assertIn("concurrent", canonical.read_text(encoding="utf-8"))
        self.assertNotIn("candidate", canonical.read_text(encoding="utf-8"))

    def test_quarantine_and_recovery_keep_canonical_project_unchanged(self) -> None:
        attempt = self.make_attempt(attempt_id="AT010")
        marker = attempt.project / "01_planning/question-register.yaml"
        marker.write_text(marker.read_text(encoding="utf-8") + "\n# crash candidate\n", encoding="utf-8")
        quarantined = quarantine_attempt(attempt, work_root=self.work, reason="crash")
        self.assertTrue(quarantined.is_dir())
        self.assertNotIn("crash candidate", (self.project / "01_planning/question-register.yaml").read_text())
        recovered = recover_quarantined_attempt(
            quarantined,
            work_root=self.work,
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id="AT010",
            role="planner",
        )
        self.assertTrue(recovered.project.is_dir())
        self.assertIn("crash candidate", recovered.project.joinpath("01_planning/question-register.yaml").read_text())

    def test_changeset_rename_and_delete_are_deterministic(self) -> None:
        baseline = snapshot_project(self.project, project_id="project/probe", run_id="HR001", task_id="TASK001", attempt_id="AT011", role="planner", protocol_root=REPO_ROOT)
        after_root = Path(tempfile.mkdtemp(dir=self.root))
        self.addCleanup(shutil.rmtree, after_root, True)
        shutil.copytree(self.project, after_root / "project")
        after_project = after_root / "project"
        source = after_project / "01_planning/question-register.yaml"
        destination = after_project / "01_planning/moved-question.yaml"
        destination.write_bytes(source.read_bytes())
        source.unlink()
        after = snapshot_project(after_project, project_id="project/probe", run_id="HR001", task_id="TASK001", attempt_id="AT011", role="planner", protocol_root=REPO_ROOT)
        changeset = build_changeset(baseline, after, project_id="project/probe", run_id="HR001", task_id="TASK001", attempt_id="AT011", role="planner", protocol_root=REPO_ROOT)
        self.assertTrue(any(change["operation"] == "RENAME" for change in changeset["changes"]))
        self.assertEqual(changeset, build_changeset(baseline, after, project_id="project/probe", run_id="HR001", task_id="TASK001", attempt_id="AT011", role="planner", protocol_root=REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
