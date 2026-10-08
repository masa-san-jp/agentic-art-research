from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from new_project import create_project  # noqa: E402
from search_harness import SearchHarnessError, record_search_requests  # noqa: E402
import task_runtime  # noqa: E402


NOW = "2026-08-25T12:00:00+09:00"
REPLAY_NOW = "2026-08-25T12:00:01+09:00"


def request(*, attempt_id: str = "AT001", request_id: str = "SR001") -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "request_id": request_id,
        "project_id": "project/probe",
        "run_id": "HR701",
        "task_id": "TASK002",
        "attempt_id": attempt_id,
        "question_id": "Q001",
        "strategy_id": "offline-fake",
        "query": "deterministic fixture query",
        "adapter": "fake",
    }


class SearchHarnessContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.output.mkdir()
        (self.work / "projects").mkdir()
        shutil.copytree(REPO_ROOT / "config", self.work / "config")
        self.project = create_project(self.work, "probe", "Probe", protocol_root=REPO_ROOT, research_route="legacy")
        (self.project / "01_planning/question-register.yaml").write_text(
            "questions:\n  - id: Q001\n    text: Which direction should continue?\n    priority: mandatory\n    status: OPEN\n",
            encoding="utf-8",
        )
        task_runtime.initialize_runtime(self.work, "project/probe", initialized_at=NOW)
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_fake_search_is_typed_and_idempotent(self) -> None:
        first = record_search_requests(
            [request()],
            protocol_root=REPO_ROOT,
            project_root=self.project,
            project_id="project/probe",
            run_id="HR701",
            task_id="TASK002",
            attempt_id="AT001",
            worker_id="worker-a",
            occurred_at=NOW,
        )
        before = (self.project / "07_runtime/run-log.jsonl").read_bytes()
        second = record_search_requests(
            [request()],
            protocol_root=REPO_ROOT,
            project_root=self.project,
            project_id="project/probe",
            run_id="HR701",
            task_id="TASK002",
            attempt_id="AT001",
            worker_id="worker-a",
            occurred_at=REPLAY_NOW,
        )
        self.assertEqual(first, second)
        self.assertEqual(before, (self.project / "07_runtime/run-log.jsonl").read_bytes())
        record = json.loads((self.project / "07_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("SEARCH_ATTEMPT", record["event_type"])
        self.assertEqual("AT001", record["attempt_id"])
        self.assertEqual("worker-a", record["worker_id"])
        self.assertTrue(record["request_sha256"].startswith("sha256:"))
        self.assertTrue(record["result_sha256"].startswith("sha256:"))
        self.assertEqual("harness.search_harness", record["recorded_by"])

    def test_unknown_question_is_rejected_before_logging(self) -> None:
        value = request()
        value["question_id"] = "Q999"
        with self.assertRaisesRegex(SearchHarnessError, "SEARCH-QUESTION-REGISTER"):
            record_search_requests(
                [value], protocol_root=REPO_ROOT, project_root=self.project,
                project_id="project/probe", run_id="HR701", task_id="TASK002", attempt_id="AT001",
                worker_id="worker-a", occurred_at=NOW,
            )
        self.assertFalse(any(
            json.loads(line).get("event_type") == "SEARCH_ATTEMPT"
            for line in (self.project / "07_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines()
        ))

    def test_same_request_id_with_changed_payload_is_rejected(self) -> None:
        record_search_requests(
            [request()], protocol_root=REPO_ROOT, project_root=self.project,
            project_id="project/probe", run_id="HR701", task_id="TASK002", attempt_id="AT001",
            worker_id="worker-a", occurred_at=NOW,
        )
        changed = request()
        changed["query"] = "different query"
        with self.assertRaisesRegex(SearchHarnessError, "SEARCH-DUPLICATE-CONFLICT"):
            record_search_requests(
                [changed], protocol_root=REPO_ROOT, project_root=self.project,
                project_id="project/probe", run_id="HR701", task_id="TASK002", attempt_id="AT001",
                worker_id="worker-a", occurred_at=NOW,
            )

    def test_request_for_another_attempt_is_rejected_without_logging(self) -> None:
        value = request(attempt_id="AT999")
        with self.assertRaisesRegex(SearchHarnessError, "SEARCH-REQUEST-IDENTITY"):
            record_search_requests(
                [value], protocol_root=REPO_ROOT, project_root=self.project,
                project_id="project/probe", run_id="HR701", task_id="TASK002", attempt_id="AT001",
                worker_id="worker-a", occurred_at=NOW,
            )
        self.assertFalse(any(
            json.loads(line).get("event_type") == "SEARCH_ATTEMPT"
            for line in (self.project / "07_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines()
        ))


if __name__ == "__main__":
    unittest.main()
