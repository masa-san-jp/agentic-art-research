from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from new_project import create_project
from task_runtime import (
    TaskRuntimeError,
    claim_next,
    complete,
    fail,
    heartbeat,
    initialize_runtime,
    load_runtime,
    resume,
)


class TaskRuntimeContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        create_project(temporary, "runtime-test", "Runtime Test", created_at="2026-08-11T00:00:00+09:00")
        return temporary

    def init(self, root: Path, definitions: list[dict]) -> None:
        initialize_runtime(root, "project/runtime-test", definitions, initialized_at="2026-08-11T00:00:00+09:00")

    def test_an_expired_lease_does_not_spend_an_attempt(self) -> None:
        """A task that took a long time did not fail; counting the expiry as a try makes length look like failure."""
        root = self.make_root()
        self.init(root, [{"id": "TASK001", "depends_on": [], "max_attempts": 2}])
        claim_next(root, "project/runtime-test", "worker-a", now="2026-08-11T00:00:00+09:00", lease_seconds=5)

        resume(root, "project/runtime-test", now="2026-08-11T00:00:06+09:00")

        state = load_runtime(root, "project/runtime-test")
        self.assertEqual(0, state["tasks"]["TASK001"]["attempts"])
        self.assertEqual(1, state["tasks"]["TASK001"]["lease_expiries"])

    def test_expiring_more_often_than_max_attempts_still_leaves_the_task_runnable(self) -> None:
        root = self.make_root()
        self.init(root, [{"id": "TASK001", "depends_on": [], "max_attempts": 2}])
        for minute in range(4):
            claim_next(root, "project/runtime-test", "worker-a",
                       now=f"2026-08-11T00:{minute:02d}:00+09:00", lease_seconds=5)
            resume(root, "project/runtime-test", now=f"2026-08-11T00:{minute:02d}:06+09:00")

        state = load_runtime(root, "project/runtime-test")
        self.assertEqual("PENDING", state["tasks"]["TASK001"]["status"])
        self.assertEqual(4, state["tasks"]["TASK001"]["lease_expiries"])

    def test_a_heartbeat_pushes_the_lease_forward(self) -> None:
        root = self.make_root()
        self.init(root, [{"id": "TASK001", "depends_on": [], "max_attempts": 2}])
        claimed = claim_next(root, "project/runtime-test", "worker-a", now="2026-08-11T00:00:00+09:00", lease_seconds=5)

        beat = heartbeat(root, "project/runtime-test", "TASK001", "worker-a", claimed["lease_token"],
                         now="2026-08-11T00:00:04+09:00", lease_seconds=60)

        self.assertGreater(beat["lease_expires_at"], claimed["lease_expires_at"])
        resume(root, "project/runtime-test", now="2026-08-11T00:00:06+09:00")
        self.assertEqual("RUNNING", load_runtime(root, "project/runtime-test")["tasks"]["TASK001"]["status"])

    def test_a_heartbeat_from_another_worker_is_refused(self) -> None:
        root = self.make_root()
        self.init(root, [{"id": "TASK001", "depends_on": [], "max_attempts": 2}])
        claimed = claim_next(root, "project/runtime-test", "worker-a", now="2026-08-11T00:00:00+09:00", lease_seconds=60)

        with self.assertRaises(TaskRuntimeError):
            heartbeat(root, "project/runtime-test", "TASK001", "worker-b", claimed["lease_token"],
                      now="2026-08-11T00:00:04+09:00", lease_seconds=60)

    def test_kill_and_resume_reclaims_lease_without_duplicate_effect(self) -> None:
        root = self.make_root()
        self.init(
            root,
            [
                {"id": "TASK001", "depends_on": [], "max_attempts": 2},
                {"id": "TASK002", "depends_on": ["TASK001"], "max_attempts": 2},
            ],
        )

        first = claim_next(root, "project/runtime-test", "worker-a", now="2026-08-11T00:00:00+09:00", lease_seconds=5)
        self.assertEqual("TASK001", first["task_id"])
        self.assertEqual(
            {"recovered": ["TASK001"], "blocked": [], "ready": ["TASK001"]},
            resume(root, "project/runtime-test", now="2026-08-11T00:00:06+09:00"),
        )
        with self.assertRaisesRegex(TaskRuntimeError, "not running"):
            complete(
                root,
                "project/runtime-test",
                "TASK001",
                "worker-a",
                first["lease_token"],
                {"value": "stale"},
                now="2026-08-11T00:00:06+09:00",
            )

        second = claim_next(root, "project/runtime-test", "worker-b", now="2026-08-11T00:00:06+09:00", lease_seconds=5)
        # The expiry returned the attempt, so this is still attempt 1. The token
        # carries the expiry count so the worker whose lease ran out cannot hand
        # back a token that now matches.
        self.assertEqual("TASK001:attempt:1:lease:1", second["lease_token"])
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        completed = complete(
            root,
            "project/runtime-test",
            "TASK001",
            "worker-b",
            second["lease_token"],
            {"value": "one-effect"},
            now="2026-08-11T00:00:07+09:00",
        )
        repeated = complete(
            root,
            "project/runtime-test",
            "TASK001",
            "worker-b",
            second["lease_token"],
            {"value": "ignored-duplicate"},
            now="2026-08-11T00:00:08+09:00",
        )
        self.assertEqual("SUCCEEDED", completed["status"])
        self.assertEqual(completed, repeated)

        dependent = claim_next(root, "project/runtime-test", "worker-c", now="2026-08-11T00:00:08+09:00")
        self.assertEqual("TASK002", dependent["task_id"])
        complete(
            root,
            "project/runtime-test",
            "TASK002",
            "worker-c",
            dependent["lease_token"],
            {"value": "done"},
            now="2026-08-11T00:00:09+09:00",
        )

        runtime = load_runtime(root, "project/runtime-test")
        self.assertEqual({"SUCCEEDED"}, {task["status"] for task in runtime["tasks"].values()})
        events = [
            json.loads(line)
            for line in (root / "projects/runtime-test/07_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len({event["event_id"] for event in events}), len(events))
        self.assertEqual(1, sum(event.get("event_type") == "TASK_SUCCEEDED" and event.get("task_id") == "TASK001" for event in events))

    def test_retry_classification_is_bounded_and_blocks_dependents(self) -> None:
        root = self.make_root()
        self.init(
            root,
            [
                {"id": "TASK001", "depends_on": [], "max_attempts": 2},
                {"id": "TASK002", "depends_on": ["TASK001"]},
            ],
        )
        with self.assertRaisesRegex(TaskRuntimeError, "unknown task failure class"):
            fail(root, "project/runtime-test", "TASK001", "worker", "missing", "UNKNOWN", "not configured")

        first = claim_next(root, "project/runtime-test", "worker", now="2026-08-11T00:00:00+09:00")
        self.assertEqual("PENDING", fail(root, "project/runtime-test", "TASK001", "worker", first["lease_token"], "TRANSIENT", "temporary", now="2026-08-11T00:00:01+09:00")["status"])
        second = claim_next(root, "project/runtime-test", "worker", now="2026-08-11T00:00:02+09:00")
        failed = fail(root, "project/runtime-test", "TASK001", "worker", second["lease_token"], "TRANSIENT", "temporary again", now="2026-08-11T00:00:03+09:00")
        self.assertEqual("FAILED", failed["status"])
        runtime = load_runtime(root, "project/runtime-test")
        self.assertEqual("FAILED", runtime["tasks"]["TASK001"]["status"])
        self.assertEqual("BLOCKED", runtime["tasks"]["TASK002"]["status"])
        self.assertEqual("DEPENDENCY_FAILED", runtime["tasks"]["TASK002"]["failure"]["class"])
        self.assertIsNone(claim_next(root, "project/runtime-test", "worker", now="2026-08-11T00:00:04+09:00"))

    def test_invalid_dag_is_rejected_before_state_is_created(self) -> None:
        root = self.make_root()
        with self.assertRaisesRegex(TaskRuntimeError, "cycle"):
            self.init(
                root,
                [
                    {"id": "TASK001", "depends_on": ["TASK002"]},
                    {"id": "TASK002", "depends_on": ["TASK001"]},
                ],
            )
        state = json.loads((root / "projects/runtime-test/07_runtime/research-state.json").read_text(encoding="utf-8"))
        self.assertNotIn("task_runtime", state)


if __name__ == "__main__":
    unittest.main()
