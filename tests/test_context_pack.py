from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from context_pack import (  # noqa: E402
    ACCEPTANCE_TESTS_PATH,
    CONTEXT_PACK_SCHEMA,
    CONSTRAINTS_PATH,
    ROLE_SOURCE_PATHS,
    build_context_pack,
)
from new_project import create_project  # noqa: E402


class ContextPackContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        shutil.copytree(REPO_ROOT / "templates", temporary / "templates")
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_every_role_receives_only_declared_sources_plus_guards(self) -> None:
        root = self.make_root()
        create_project(root, "context-test", "Context Test")
        for role, declared in ROLE_SOURCE_PATHS.items():
            with self.subTest(role=role):
                pack = build_context_pack(root, "project/context-test", "TASK001", role)
                self.assertEqual(CONTEXT_PACK_SCHEMA, pack["schema"])
                self.assertEqual("project/context-test", pack["project_id"])
                self.assertEqual("TASK001", pack["task_id"])
                self.assertEqual(role, pack["role"])
                self.assertEqual(CONSTRAINTS_PATH, pack["constraints"]["path"])
                self.assertEqual(ACCEPTANCE_TESTS_PATH, pack["acceptance_tests"]["path"])
                self.assertEqual(list(declared), [item["path"] for item in pack["evidence"]])

    def test_task_selection_is_minimal_and_deterministic(self) -> None:
        root = self.make_root()
        create_project(root, "context-task", "Context Task")
        first = build_context_pack(root, "project/context-task", "TASK001", "planner")
        second = build_context_pack(root, "project/context-task", "TASK001", "planner")
        self.assertEqual(first, second)
        # The pack carries the task as the plan states it, and a task now states
        # which role runs it. It still carries only that task.
        self.assertEqual({"id", "title", "depends_on", "role"}, set(first["task"]))
        self.assertEqual("TASK001", first["task"]["id"])
        self.assertNotIn("tasks", first["task"])

    def test_missing_source_and_unknown_task_fail_closed(self) -> None:
        root = self.make_root()
        project = create_project(root, "context-errors", "Context Errors")
        (project / ROLE_SOURCE_PATHS["planner"][0]).unlink()
        with self.assertRaisesRegex(FileNotFoundError, "context source not found"):
            build_context_pack(root, "project/context-errors", "TASK001", "planner")
        with self.assertRaisesRegex(ValueError, "task not found"):
            build_context_pack(root, "project/context-errors", "TASK999", "planner")

    def test_unknown_role_fails_closed(self) -> None:
        root = self.make_root()
        create_project(root, "context-role", "Context Role")
        with self.assertRaisesRegex(ValueError, "unknown role"):
            build_context_pack(root, "project/context-role", "TASK001", "unknown")


if __name__ == "__main__":
    unittest.main()
