from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from _common import load_json, stable_json  # noqa: E402
from acceptance_executor import (  # noqa: E402
    AcceptanceExecutorError,
    complete_attempt,
    execute_acceptance,
    recover_attempt_transaction,
)
import acceptance_executor  # noqa: E402
from attempt_workspace import create_attempt_workspace  # noqa: E402
from new_project import create_project  # noqa: E402
import task_runtime  # noqa: E402


NOW = "2026-08-25T10:00:00+09:00"


def _tree_digest(root: Path, excluded: set[str] | None = None) -> tuple[tuple[str, str], ...]:
    excluded = excluded or set()
    return tuple(
        (
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(
            item for item in root.rglob("*")
            if item.is_file() and item.relative_to(root).as_posix() not in excluded
        )
    )


class AcceptanceExecutorContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.output.mkdir()
        (self.work / "projects").mkdir()
        (self.work / "data").mkdir()
        shutil.copytree(REPO_ROOT / "config", self.work / "config")
        self.project = create_project(self.work, "probe", "Probe", creator_id="test", protocol_root=REPO_ROOT)
        task_runtime.initialize_runtime(self.work, "project/probe", initialized_at=NOW)
        claimed = task_runtime.claim_next(self.work, "project/probe", "worker-1", now=NOW)
        self.assertIsNotNone(claimed)
        self.lease_token = claimed["lease_token"]
        self.attempt = create_attempt_workspace(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id="AT001",
            role="planner",
        )
        self.addCleanup(shutil.rmtree, self.root, True)

    def _edit_question_register(self) -> None:
        path = self.attempt.project / "01_planning/question-register.yaml"
        path.write_text(
            "questions:\n"
            "  - id: Q001\n"
            "    text: Which visual interval should be tested?\n"
            "    priority: mandatory\n"
            "    status: OPEN\n"
            "\n# typed acceptance candidate\n",
            encoding="utf-8",
        )

    def test_success_promotes_changeset_and_completes_only_through_the_executor(self) -> None:
        self._edit_question_register()

        result = complete_attempt(
            self.attempt,
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            worker_id="worker-1",
            lease_token=self.lease_token,
            evaluated_at=NOW,
        )

        self.assertTrue(result["promoted"], result)
        self.assertEqual("PASS", result["report"]["status"], result)
        self.assertEqual("SUCCEEDED", result["task"]["status"])
        self.assertIn("typed acceptance candidate", (self.project / "01_planning/question-register.yaml").read_text())
        self.assertTrue(result["effect_key"].startswith("acceptance/sha256:"))
        self.assertEqual(result, complete_attempt(
            self.attempt,
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            worker_id="worker-1",
            lease_token=self.lease_token,
            evaluated_at=NOW,
        ))

    def test_failed_gate_leaves_worker_data_unpromoted_and_schedules_validation_retry(self) -> None:
        path = self.attempt.project / "01_planning/question-register.yaml"
        document = path.read_text(encoding="utf-8")
        path.write_text(document.replace("questions:\n", "questions: []\n", 1), encoding="utf-8")
        before = _tree_digest(self.project, excluded={"07_runtime/research-state.json", "07_runtime/run-log.jsonl"})

        result = complete_attempt(
            self.attempt,
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            worker_id="worker-1",
            lease_token=self.lease_token,
            evaluated_at=NOW,
        )

        self.assertFalse(result["promoted"])
        self.assertEqual("FAIL", result["report"]["status"])
        self.assertEqual("PENDING", result["task"]["status"])
        self.assertEqual(
            before,
            _tree_digest(self.project, excluded={"07_runtime/research-state.json", "07_runtime/run-log.jsonl"}),
        )
        self.assertTrue(any(gate["status"] == "FAIL" for gate in result["report"]["gates"]))

    def test_post_promotion_complete_failure_restores_project_and_runtime_bytes(self) -> None:
        self._edit_question_register()
        before_project = _tree_digest(self.project)
        before_state = (self.project / "07_runtime/research-state.json").read_bytes()
        before_log = (self.project / "07_runtime/run-log.jsonl").read_bytes()

        with mock.patch("acceptance_executor.task_runtime.complete", side_effect=RuntimeError("injected completion fault")):
            with self.assertRaisesRegex(AcceptanceExecutorError, "ACCEPTANCE-ROLLBACK"):
                complete_attempt(
                    self.attempt,
                    protocol_root=REPO_ROOT,
                    work_root=self.work,
                    output_root=self.output,
                    worker_id="worker-1",
                    lease_token=self.lease_token,
                    evaluated_at=NOW,
                )

        self.assertEqual(before_project, _tree_digest(self.project))
        self.assertEqual(before_state, (self.project / "07_runtime/research-state.json").read_bytes())
        self.assertEqual(before_log, (self.project / "07_runtime/run-log.jsonl").read_bytes())
        self.assertEqual("RUNNING", load_json(self.project / "07_runtime/research-state.json")["task_runtime"]["tasks"]["TASK001"]["status"])

    def test_recovery_restores_a_process_crash_after_candidate_swap(self) -> None:
        self._edit_question_register()
        before = _tree_digest(self.project)
        real_promote = acceptance_executor.promote_attempt

        def promote_then_crash(*args, **kwargs):
            real_promote(*args, **kwargs)
            raise RuntimeError("injected process crash after candidate swap")

        with mock.patch("acceptance_executor.promote_attempt", side_effect=promote_then_crash):
            with self.assertRaisesRegex(AcceptanceExecutorError, "ACCEPTANCE-ROLLBACK"):
                complete_attempt(
                    self.attempt,
                    protocol_root=REPO_ROOT,
                    work_root=self.work,
                    output_root=self.output,
                    worker_id="worker-1",
                    lease_token=self.lease_token,
                    evaluated_at=NOW,
                )

        self.assertEqual("PREPARED", load_json(self.attempt.root / "transaction.json")["stage"])
        recovered = recover_attempt_transaction(
            self.attempt,
            protocol_root=REPO_ROOT,
            work_root=self.work,
        )
        self.assertEqual("ROLLED_BACK", recovered["stage"])
        self.assertEqual(before, _tree_digest(self.project))

    def test_report_is_deterministic_and_idempotent(self) -> None:
        self._edit_question_register()
        checks = [{"id": "AG-TEST-VALIDATE", "kind": "project_validate"}]
        first = execute_acceptance(
            protocol_root=REPO_ROOT,
            attempt_project=self.attempt.project,
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id="AT001",
            role="planner",
            evaluated_at=NOW,
            checks=checks,
            report_path=self.attempt.root / "acceptance-report.json",
        )
        second = execute_acceptance(
            protocol_root=REPO_ROOT,
            attempt_project=self.attempt.project,
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id="AT001",
            role="planner",
            evaluated_at=NOW,
            checks=checks,
            report_path=self.attempt.root / "acceptance-report.json",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["report_id"], "AR-" + hashlib.sha256(
            stable_json({key: value for key, value in first.items() if key != "report_id"}).encode()
        ).hexdigest()[:16])

    def test_unknown_kind_and_absolute_path_are_named(self) -> None:
        common = {
            "protocol_root": REPO_ROOT,
            "attempt_project": self.attempt.project,
            "project_id": "project/probe",
            "run_id": "HR001",
            "task_id": "TASK001",
            "attempt_id": "AT001",
            "role": "planner",
            "evaluated_at": NOW,
        }
        with self.assertRaisesRegex(AcceptanceExecutorError, "ACCEPTANCE-CHECK-UNKNOWN"):
            execute_acceptance(**common, checks=[{"id": "AG-TEST-UNKNOWN", "kind": "shell"}])
        with self.assertRaisesRegex(AcceptanceExecutorError, "ACCEPTANCE-CHECK-SCHEMA"):
            execute_acceptance(**common, checks=[{"id": "AG-TEST-PATH", "kind": "collection_minimum", "path": "/tmp/out"}])

    def test_cli_cannot_complete_without_the_harness_boundary(self) -> None:
        command = [
            sys.executable,
            "tools/task_runtime.py",
            "project/probe",
            "complete",
            "--root",
            str(self.work),
            "--task-id",
            "TASK001",
            "--worker-id",
            "worker-1",
            "--lease-token",
            self.lease_token,
            "--result-json",
            "{}",
            "--now",
            NOW,
        ]
        completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("TASK-COMPLETE-WITHOUT-GATE", completed.stderr)


if __name__ == "__main__":
    unittest.main()
