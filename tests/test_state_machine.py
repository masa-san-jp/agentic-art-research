from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "harmony"
sys.path.insert(0, str(REPO_ROOT / "tools"))

from new_project import create_project
from run_project import run_offline_fixture
from state_machine import TransitionError, load_state_machine, replay_project, transition_project
from validate import validate_repository


class StateMachineContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_offline_fixture_run_log_replays_to_the_research_state(self) -> None:
        root = self.make_root()
        run_offline_fixture(root, "harmony-study", FIXTURE)
        result = replay_project(root, "project/harmony-study")
        self.assertEqual("DRAFT", result.initial_status)
        self.assertEqual("COMPLETE_WITH_GAPS", result.final_status)
        self.assertEqual(8, result.event_count)
        self.assertEqual(8, result.transition_count)

    def test_illegal_and_discontinuous_events_are_rejected(self) -> None:
        machine = load_state_machine(REPO_ROOT)
        with self.assertRaisesRegex(TransitionError, "illegal lifecycle transition"):
            machine.apply("DRAFT", {"from_status": "DRAFT", "to_status": "COMPLETE"})
        with self.assertRaisesRegex(TransitionError, "current status"):
            machine.apply("PLANNED", {"from_status": "DRAFT", "to_status": "PLANNED"})
        with self.assertRaisesRegex(TransitionError, "duplicate event ID"):
            machine.replay(
                "DRAFT",
                [
                    {"event_id": "EVT001", "event_type": "TASK", "status": "started"},
                    {"event_id": "EVT001", "event_type": "TASK", "status": "finished"},
                ],
            )
        with self.assertRaisesRegex(TransitionError, "current status"):
            machine.replay(
                "DRAFT",
                [
                    {"event_id": "EVT001", "event_type": "STATE_TRANSITION", "from_status": "DRAFT", "to_status": "PLANNED"},
                    {"event_id": "EVT002", "event_type": "STATE_TRANSITION", "from_status": "DRAFT", "to_status": "BLOCKED"},
                ],
            )

    def test_transition_project_applies_valid_event_and_preserves_invalid_attempt(self) -> None:
        root = self.make_root()
        create_project(root, "state-test", "State Test", created_at="2026-08-11T00:00:00+09:00")
        event = transition_project(
            root,
            "project/state-test",
            "PLANNED",
            event_id="EVT001",
            updated_at="2026-08-11T00:10:00+09:00",
        )
        self.assertEqual("DRAFT", event["from_status"])
        self.assertEqual("PLANNED", event["to_status"])
        self.assertEqual("PLANNED", replay_project(root, "project/state-test").final_status)
        self.assertEqual([], validate_repository(root))
        state_path = root / "projects/state-test/07_runtime/research-state.json"
        before = state_path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(TransitionError, "illegal lifecycle transition"):
            transition_project(
                root,
                "project/state-test",
                "COMPLETE",
                event_id="EVT002",
                updated_at="2026-08-11T00:20:00+09:00",
            )
        self.assertEqual(before, state_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
