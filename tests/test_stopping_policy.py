from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from new_project import create_project
from stopping_policy import StoppingPolicy, StoppingPolicyError, apply_project, evaluate_question, evaluate_project
from validate import validate_repository


class StoppingPolicyContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        create_project(temporary, "stopping-test", "Stopping Test", created_at="2026-08-11T00:00:00+09:00")
        return temporary

    def write_questions(self, root: Path, questions: list[dict]) -> None:
        (root / "projects/stopping-test/01_planning/question-register.yaml").write_text(
            yaml.safe_dump({"questions": questions}, sort_keys=False), encoding="utf-8"
        )

    def write_events(self, root: Path, events: list[dict]) -> None:
        (root / "projects/stopping-test/07_runtime/run-log.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )

    def test_success_wins_and_limits_terminalize_without_third_repeat(self) -> None:
        root = self.make_root()
        policy = StoppingPolicy.from_root(root)
        question = {
            "id": "Q001",
            "status": "OPEN",
            "stop_condition": {"sufficient_answers": 1, "max_search_strategies": 1},
        }
        answered = evaluate_question(question, {"answers": 1, "search_strategies": ["strategy-1"]}, policy)
        self.assertEqual("ANSWERED", answered["status"])
        blocked = evaluate_question(
            question,
            {"failure_counts": {"timeout": 2}, "search_strategies": [], "sources_reviewed": []},
            policy,
        )
        self.assertEqual("BLOCKED", blocked["status"])
        with self.assertRaisesRegex(StoppingPolicyError, "positive integer"):
            evaluate_question({"id": "Q001", "status": "OPEN", "stop_condition": {"max_sources_reviewed": 0}}, {}, policy)

    def test_saturation_is_consecutive_and_apply_is_idempotent(self) -> None:
        root = self.make_root()
        self.write_questions(
            root,
            [{"id": "Q001", "text": "Question", "priority": "mandatory", "status": "OPEN"}],
        )
        self.write_events(
            root,
            [
                {"event_id": "EVT001", "event_type": "EVIDENCE_ROUND", "question_id": "Q001", "new_evidence_count": 0},
                {"event_id": "EVT002", "event_type": "EVIDENCE_ROUND", "question_id": "Q001", "new_evidence_count": 2},
                {"event_id": "EVT003", "event_type": "EVIDENCE_ROUND", "question_id": "Q001", "new_evidence_count": 0},
                {"event_id": "EVT004", "event_type": "EVIDENCE_ROUND", "question_id": "Q001", "new_evidence_count": 0},
            ],
        )
        evaluation = evaluate_project(root, "project/stopping-test")
        self.assertTrue(evaluation["terminal"])
        self.assertEqual("UNRESOLVED", evaluation["decisions"][0]["status"])
        applied = apply_project(root, "project/stopping-test", evaluated_at="2026-08-11T00:10:00+09:00")
        repeated = apply_project(root, "project/stopping-test", evaluated_at="2026-08-11T00:10:00+09:00")
        self.assertEqual(1, len(applied["changed"]))
        self.assertEqual([], repeated["changed"])
        question = yaml.safe_load(
            (root / "projects/stopping-test/01_planning/question-register.yaml").read_text(encoding="utf-8")
        )["questions"][0]
        self.assertEqual("UNRESOLVED", question["status"])
        self.assertEqual(1, sum("STOPPING_DECISION" == event["event_type"] for event in map(json.loads, (root / "projects/stopping-test/07_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines())))
        self.assertEqual([], validate_repository(root))


if __name__ == "__main__":
    unittest.main()
