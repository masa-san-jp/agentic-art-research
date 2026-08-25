from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import harness_supervisor  # noqa: E402
import task_runtime  # noqa: E402
from _common import atomic_write_text, load_json, stable_json  # noqa: E402
from new_project import create_project  # noqa: E402


NOW = "2026-08-25T12:00:00+09:00"


def success_result(request: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "run_id": request["run_id"],
        "attempt_id": request["attempt_id"],
        "status": "SUCCEEDED",
        "summary": "Supervisor test worker completed.",
        "effect_key": f"attempt/{request['attempt_id']}",
        "outputs": [],
        "failure": None,
        "human_decision_request": None,
        "diagnostics": {"stderr": "", "exit_code": 0, "signal": None, "timed_out": False},
    }


class HarnessSupervisorContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.output.mkdir()
        (self.work / "projects").mkdir()
        shutil.copytree(REPO_ROOT / "config", self.work / "config")
        self.project = create_project(self.work, "supervisor-probe", "Supervisor probe", protocol_root=REPO_ROOT)
        plan = self.project / "01_planning/research-plan.yaml"
        plan.write_text(
            plan.read_text(encoding="utf-8").replace(
                "  - id: TASK001\n    role: planner\n    title: Fix the questions and the plan\n    depends_on: []\n",
                "  - id: TASK001\n    role: planner\n    title: Fix the questions and the plan\n    depends_on: []\n",
            ).split("  - id: TASK002", 1)[0],
            encoding="utf-8",
        )
        task_runtime.initialize_runtime(self.work, "project/supervisor-probe", initialized_at=NOW)
        self.addCleanup(shutil.rmtree, self.root, True)

    def worker(self, request_path: Path, *, output_path: Path, **kwargs: object) -> dict[str, object]:
        request = load_json(request_path)
        attempt_project = Path(request["attempt_workspace"])
        (attempt_project / "01_planning/question-register.yaml").write_text(
            "questions:\n  - id: Q001\n    question: Which direction should continue?\n    status: OPEN\n    stop_condition:\n      sufficient_answers: 1\n",
            encoding="utf-8",
        )
        result = success_result(request)
        atomic_write_text(output_path, stable_json(result))
        return result

    def make_supervisor(self, **overrides: object) -> harness_supervisor.Supervisor:
        values: dict[str, object] = {
            "protocol_root": REPO_ROOT,
            "work_root": self.work,
            "output_root": self.output,
            "project_id": "project/supervisor-probe",
            "run_id": "HR701",
            "worker_id": "worker-supervisor",
            "now": lambda: NOW,
            "sleep": lambda _seconds: None,
            "worker_runner": self.worker,
            "max_tasks": 3,
        }
        values.update(overrides)
        return harness_supervisor.Supervisor(**values)

    def test_run_claims_attempt_promotes_and_is_idempotent(self) -> None:
        result = self.make_supervisor().run()
        self.assertEqual("SUCCEEDED", result["status"])
        runtime = load_json(self.project / "07_runtime/research-state.json")["task_runtime"]
        self.assertEqual("SUCCEEDED", runtime["tasks"]["TASK001"]["status"])
        journal = load_json(self.root / "work/.harness/supervisor/supervisor-probe/HR701.json")
        self.assertEqual("SUCCEEDED", journal["status"])
        self.assertTrue(all("/" not in json.dumps(event) for event in journal["events"]))
        again = self.make_supervisor().run(resume=True)
        self.assertEqual(result, again)
        self.assertEqual(1, len(list((self.work / ".harness/attempts/HR701/TASK001").glob("AT*/result.json"))))

    def test_retryable_worker_failure_uses_configured_class_and_retries(self) -> None:
        calls = {"count": 0}

        def flaky(request_path: Path, *, output_path: Path, **kwargs: object) -> dict[str, object]:
            request = load_json(request_path)
            calls["count"] += 1
            if calls["count"] == 1:
                result = success_result(request)
                result["status"] = "FAILED"
                result["effect_key"] = None
                result["failure"] = {"class": "WORKER-EXIT", "message": "synthetic crash"}
            else:
                result = self.worker(request_path, output_path=output_path, **kwargs)
            atomic_write_text(output_path, stable_json(result))
            return result

        result = self.make_supervisor(worker_runner=flaky, max_tasks=4).run()
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual(2, calls["count"])
        events = load_json(self.root / "work/.harness/supervisor/supervisor-probe/HR701.json")["events"]
        self.assertTrue(any(event.get("phase") == "RETRY_WAIT" and event.get("failure_class") == "TRANSIENT" for event in events))

    def test_heartbeat_is_recorded_and_shutdown_is_resumable(self) -> None:
        def long_worker(request_path: Path, *, output_path: Path, heartbeat_callback: object, **kwargs: object) -> dict[str, object]:
            request = load_json(request_path)
            heartbeat_callback()
            result = self.worker(request_path, output_path=output_path, **kwargs)
            return result

        result = self.make_supervisor(worker_runner=long_worker, max_tasks=3).run()
        self.assertEqual("SUCCEEDED", result["status"])
        events = load_json(self.root / "work/.harness/supervisor/supervisor-probe/HR701.json")["events"]
        self.assertTrue(any(event.get("phase") == "HEARTBEAT" for event in events))

        create_project(self.work, "shutdown-probe", "Shutdown probe", protocol_root=REPO_ROOT)
        task_runtime.initialize_runtime(self.work, "project/shutdown-probe", initialized_at=NOW)
        shutdown_supervisor = self.make_supervisor(project_id="project/shutdown-probe", run_id="HR702")

        def stopping_worker(request_path: Path, *, heartbeat_callback: object, **kwargs: object) -> dict[str, object]:
            shutdown_supervisor.shutdown_requested = True
            heartbeat_callback()
            raise AssertionError("shutdown must stop the worker before it produces an effect")

        shutdown_supervisor.worker_runner = stopping_worker
        paused = shutdown_supervisor.run()
        self.assertEqual("SHUTDOWN", paused["status"])
        self.assertIn("python3 tools/harness.py resume project/shutdown-probe --run-id HR702", paused["resume_command"])

    def test_human_required_is_a_paused_outcome(self) -> None:
        def needs_human(request_path: Path, *, output_path: Path, **kwargs: object) -> dict[str, object]:
            request = load_json(request_path)
            result = {
                **success_result(request),
                "status": "HUMAN_REQUIRED",
                "effect_key": None,
                "failure": None,
                "human_decision_request": {
                    "id": "DR701",
                    "project_id": "project/supervisor-probe",
                    "task_id": request["task_id"],
                    "human_decision_category": "CENTRAL_PROPOSITION_CHANGE",
                    "question": "Which direction should continue?",
                    "facts": [{"id": "F701", "statement": "Two directions remain viable."}],
                    "options": [
                        {"id": "OPT-a", "label": "A", "consequence": "Keep scope."},
                        {"id": "OPT-b", "label": "B", "consequence": "Change scope."},
                    ],
                    "recommended_option": "OPT-a",
                    "impact": "The selected direction changes downstream work.",
                    "default_safe_action": "DEFER",
                    "source_refs": ["fixture/source-701"],
                },
            }
            atomic_write_text(output_path, stable_json(result))
            return result

        result = self.make_supervisor(worker_runner=needs_human).run()
        self.assertEqual("PAUSED", result["status"])
        runtime = load_json(self.project / "07_runtime/research-state.json")["task_runtime"]
        self.assertEqual("WAITING_HUMAN", runtime["tasks"]["TASK001"]["status"])

    def test_resume_reuses_durable_result_after_worker_process_crash(self) -> None:
        calls = {"count": 0}

        def crash_after_result(request_path: Path, *, output_path: Path, **kwargs: object) -> dict[str, object]:
            calls["count"] += 1
            result = self.worker(request_path, output_path=output_path, **kwargs)
            raise RuntimeError("synthetic process crash after durable result")

        first = self.make_supervisor(worker_runner=crash_after_result, max_tasks=3)
        with self.assertRaisesRegex(RuntimeError, "synthetic process crash"):
            first.run()

        def must_not_run(*args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("durable result must be reused during resume")

        resumed = self.make_supervisor(
            now=lambda: "2026-08-25T13:00:00+09:00",
            worker_runner=must_not_run,
            max_tasks=3,
        ).run(resume=True)
        self.assertEqual("SUCCEEDED", resumed["status"])
        self.assertEqual(1, calls["count"])
        self.assertTrue(any(event.get("phase") == "RESULT_RECORDED" for event in resumed["events"]))

    def test_second_supervisor_is_rejected_by_run_lock(self) -> None:
        first = self.make_supervisor()
        with harness_supervisor._run_lock(self.work, "project/supervisor-probe", "HR701"):
            with self.assertRaisesRegex(harness_supervisor.SupervisorError, "HARNESS-RUN-LOCKED"):
                first.run()

    def test_harness_run_cli_returns_machine_readable_human_pause(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "tools/harness.py",
                "run",
                "project/supervisor-probe",
                "--protocol-root",
                str(REPO_ROOT),
                "--work-root",
                str(self.work),
                "--output-root",
                str(self.output),
                "--run-id",
                "HR703",
                "--worker",
                "cli-supervisor",
                "--fixture-mode",
                "human_required",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual("PAUSED", json.loads(completed.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
