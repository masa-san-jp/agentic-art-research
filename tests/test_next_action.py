from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import log_event
import next_action
import task_runtime


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-21T09:00:00+09:00"
LATER = "2026-08-21T09:10:00+09:00"


def _new_project(root: Path, slug: str) -> None:
    subprocess.run(
        [sys.executable, "tools/new_project.py", slug, "--title", slug, "--creator-id", "test", "--root", str(root)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )


class RoleResolutionTests(unittest.TestCase):
    TABLE = {
        "default_role": "collector",
        "by_task_id": {"TASK001": "planner"},
        "by_title_contains": [{"match": "Analyze", "role": "analyst"}],
    }

    def test_a_task_that_declares_its_role_keeps_it(self):
        role = next_action.resolve_role(self.TABLE, {"role": "critic", "title": "Analyze"}, "TASK001")

        self.assertEqual("critic", role)

    def test_a_plan_written_before_roles_existed_falls_back_to_the_task_id(self):
        role = next_action.resolve_role(self.TABLE, {"title": "Validate intake"}, "TASK001")

        self.assertEqual("planner", role)

    def test_an_unlisted_task_id_falls_back_to_the_title(self):
        role = next_action.resolve_role(self.TABLE, {"title": "Analyze evidence"}, "TASK009")

        self.assertEqual("analyst", role)

    def test_a_task_matching_nothing_still_gets_a_role(self):
        role = next_action.resolve_role(self.TABLE, {"title": "Something else"}, "TASK009")

        self.assertEqual("collector", role)


class ProtocolExtractionTests(unittest.TestCase):
    TEXT = "# t\n\n## 1. First\n\nbody one\n\n## 2. Second\n\nbody two\n\n## 3. Third\n\nbody three\n"

    def test_only_the_requested_sections_are_returned(self):
        sections = next_action.protocol_sections(self.TEXT, [1, 3])

        self.assertEqual([1, 3], [section["section"] for section in sections])

    def test_the_section_body_travels_with_its_title(self):
        sections = next_action.protocol_sections(self.TEXT, [2])

        self.assertEqual("Second", sections[0]["title"])
        self.assertEqual("body two", sections[0]["body"])


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        (self.project / "01_planning").mkdir(parents=True)
        (self.project / "01_planning/research-plan.yaml").write_text(
            "budget:\n  max_questions: 2\n  max_total_sources: 2\n", encoding="utf-8")
        self.addCleanup(self.temporary.cleanup)

    def test_a_run_that_stayed_inside_its_budget_reports_no_excess(self):
        events = [{"event_type": "SOURCE_REVIEWED", "question_id": "Q001", "source_id": "S1"}]

        budget = next_action.budget_remaining(self.project, events)

        self.assertEqual([], budget["exceeded"])

    def test_spending_past_a_limit_is_named(self):
        events = [
            {"event_type": "SOURCE_REVIEWED", "question_id": "Q001", "source_id": f"S{index}"}
            for index in range(3)
        ]

        budget = next_action.budget_remaining(self.project, events)

        self.assertIn("max_total_sources", budget["exceeded"])

    def test_the_same_source_seen_twice_is_spent_once(self):
        events = [{"event_type": "SOURCE_REVIEWED", "question_id": "Q001", "source_id": "S1"}] * 3

        budget = next_action.budget_remaining(self.project, events)

        self.assertEqual(1, budget["spent"]["total_sources"])


class EntryPointTests(unittest.TestCase):
    """The entry hands over one task with everything needed to finish it."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("config", "schemas", "docs", "templates", "tools"):
            shutil.copytree(ROOT / name, self.root / name)
        (self.root / "projects").mkdir()
        _new_project(self.root, "probe")
        task_runtime.initialize_runtime(self.root, "project/probe", initialized_at=NOW)
        self.addCleanup(self.temporary.cleanup)

    def test_the_answer_carries_every_part_the_agent_needs(self):
        answer = next_action.build_next_action(self.root, "project/probe", "tester", NOW)

        for key in ("task_id", "role", "context", "budget_remaining", "stopping",
                    "instructions", "write_targets", "acceptance", "forbidden"):
            self.assertIn(key, answer, key)

    def test_the_role_comes_from_the_plan_not_from_the_id_table(self):
        """The runtime keeps only scheduling fields, so reading the role from it hands over another role's work."""
        project = self.root / "projects/probe"
        plan_path = project / "01_planning/research-plan.yaml"
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan["tasks"][0]["role"] = "critic"
        plan_path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")

        answer = next_action.build_next_action(self.root, "project/probe", "tester", NOW)

        self.assertEqual("critic", answer["role"])

    def test_acceptance_commands_name_the_actual_project(self):
        answer = next_action.build_next_action(self.root, "project/probe", "tester", NOW)

        self.assertTrue(all("{slug}" not in command for command in answer["acceptance"]))
        self.assertTrue(any("probe" in command for command in answer["acceptance"]))

    def test_asking_again_returns_the_held_task_instead_of_taking_another_attempt(self):
        first = next_action.build_next_action(self.root, "project/probe", "tester", NOW)

        second = next_action.build_next_action(self.root, "project/probe", "tester", LATER)

        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual("TASK_RESUMED", second["status"])

    def test_the_held_task_is_only_returned_to_the_worker_holding_it(self):
        next_action.build_next_action(self.root, "project/probe", "tester", NOW)

        other = next_action.build_next_action(self.root, "project/probe", "someone-else", LATER)

        self.assertNotEqual("TASK_RESUMED", other["status"])


class TaskTemplateTests(unittest.TestCase):
    """One task is one session, so the plan is split into sessions rather than into phases."""

    PLAN = yaml.safe_load((ROOT / "templates/project/01_planning/research-plan.yaml").read_text(encoding="utf-8"))

    def test_every_task_declares_the_role_that_runs_it(self):
        self.assertTrue(all(task.get("role") for task in self.PLAN["tasks"]))

    def test_every_declared_role_is_described_in_the_role_table(self):
        table = yaml.safe_load((ROOT / "config/task-roles.yaml").read_text(encoding="utf-8"))

        for task in self.PLAN["tasks"]:
            self.assertIn(task["role"], table["roles"], task["id"])

    def test_evidence_is_collected_one_question_at_a_time(self):
        collectors = [task for task in self.PLAN["tasks"] if task["role"] == "collector"]

        self.assertGreater(len(collectors), 1)

    def test_the_plan_reaches_a_validator(self):
        self.assertEqual("validator", self.PLAN["tasks"][-1]["role"])


class BudgetEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("config", "schemas", "docs", "templates", "tools"):
            shutil.copytree(ROOT / name, self.root / name)
        (self.root / "projects").mkdir()
        _new_project(self.root, "spent")
        task_runtime.initialize_runtime(self.root, "project/spent", initialized_at=NOW)
        project = self.root / "projects/spent"
        plan_path = project / "01_planning/research-plan.yaml"
        plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan["budget"] = {"max_questions": 1, "max_total_sources": 1}
        plan_path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (project / "01_planning/question-register.yaml").write_text(
            yaml.safe_dump({"questions": [{"id": "Q001", "question": "spent", "priority": "preferred",
                                           "status": "OPEN"}]}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        (project / "07_runtime/run-log.jsonl").write_text(
            "".join(
                json.dumps({"event_id": f"E{index}", "event_type": "SOURCE_REVIEWED",
                            "question_id": "Q001", "source_id": f"S{index}"}) + "\n"
                for index in range(4)
            ),
            encoding="utf-8",
        )
        self.addCleanup(self.temporary.cleanup)

    def test_a_project_past_its_budget_is_not_given_another_task(self):
        answer = next_action.build_next_action(self.root, "project/spent", "tester", NOW)

        self.assertEqual("BUDGET_EXCEEDED", answer["status"])
        self.assertNotIn("task_id", answer)

    def test_the_answer_says_how_to_close_out_instead_of_stopping(self):
        answer = next_action.build_next_action(self.root, "project/spent", "tester", NOW)

        self.assertTrue(answer["directive"])
        self.assertTrue(answer["next_steps"])

    def test_a_task_already_in_hand_is_still_finished(self):
        held = {"tasks": {"TASK001": {"status": "RUNNING",
                                      "lease": {"owner": "tester", "token": "t", "expires_at": LATER}}}}

        claim = next_action._held_by(held, "tester", NOW)

        self.assertEqual("TASK001", claim["task_id"])

class LogEventTests(unittest.TestCase):
    def setUp(self):
        self.events: list[dict] = []

    def test_a_search_without_a_strategy_is_refused(self):
        with self.assertRaises(log_event.EventError):
            log_event.build_event(self.events, "SEARCH_ATTEMPT", occurred_at=NOW, question_id="Q001")

    def test_a_reviewed_source_without_an_identifier_is_refused(self):
        with self.assertRaises(log_event.EventError):
            log_event.build_event(self.events, "SOURCE_REVIEWED", occurred_at=NOW, question_id="Q001")

    def test_an_event_without_a_question_is_refused(self):
        with self.assertRaises(log_event.EventError):
            log_event.build_event(self.events, "ANSWER_FOUND", occurred_at=NOW)

    def test_an_unknown_event_type_is_refused(self):
        with self.assertRaises(log_event.EventError):
            log_event.build_event(self.events, "SOMETHING_ELSE", occurred_at=NOW, question_id="Q001")

    def test_a_complete_search_event_is_recorded_with_its_strategy(self):
        event = log_event.build_event(
            self.events, "SEARCH_ATTEMPT", occurred_at=NOW, question_id="Q001", strategy_id="catalogue")

        self.assertEqual("catalogue", event["strategy_id"])

    def test_event_ids_do_not_repeat(self):
        first = log_event.build_event(
            self.events, "SEARCH_ATTEMPT", occurred_at=NOW, question_id="Q001", strategy_id="a")

        second = log_event.build_event(
            [first], "SEARCH_ATTEMPT", occurred_at=NOW, question_id="Q001", strategy_id="b")

        self.assertNotEqual(first["event_id"], second["event_id"])


if __name__ == "__main__":
    unittest.main()
