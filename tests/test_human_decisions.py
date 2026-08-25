from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from canonical import canonical_sha256  # noqa: E402
from harness import HarnessError, record_attempt_result  # noqa: E402
from human_decisions import (  # noqa: E402
    HumanDecisionError,
    build_request,
    record_human_required,
    record_request,
    resolve_request,
    unresolved_requests,
)
from new_project import create_project  # noqa: E402
from next_action import build_next_action  # noqa: E402
import task_runtime  # noqa: E402
from _common import load_json, stable_json  # noqa: E402


NOW = "2026-08-25T12:00:00+09:00"


class HumanDecisionContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.output.mkdir()
        (self.work / "projects").mkdir()
        shutil.copytree(REPO_ROOT / "config", self.work / "config")
        self.project = create_project(self.work, "probe", "Probe", creator_id="test", protocol_root=REPO_ROOT)
        task_runtime.initialize_runtime(self.work, "project/probe", initialized_at=NOW)
        claimed = task_runtime.claim_next(self.work, "project/probe", "worker-a", now=NOW)
        self.assertIsNotNone(claimed)
        self.lease_token = claimed["lease_token"]
        self.addCleanup(shutil.rmtree, self.root, True)

    def request_input(self, *, category: str = "CENTRAL_PROPOSITION_CHANGE") -> dict[str, object]:
        return {
            "id": "DR001",
            "project_id": "project/probe",
            "task_id": "TASK001",
            "human_decision_category": category,
            "question": "Which reviewed direction should continue?",
            "facts": [{"id": "F001", "statement": "Two directions remain viable.", "source_refs": ["fixture/source-001"]}],
            "options": [
                {"id": "OPT-a", "label": "Direction A", "consequence": "Keep the current scope."},
                {"id": "OPT-b", "label": "Direction B", "consequence": "Change the central proposition."},
            ],
            "recommended_option": "OPT-a",
            "impact": "The choice changes the downstream production translation.",
            "default_safe_action": "DEFER",
            "source_refs": ["fixture/source-001"],
        }

    def request(self) -> dict[str, object]:
        return build_request(
            self.request_input(),
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id="AT001",
            created_at=NOW,
        )

    def response(self, *, response_id: str = "DRR001", request_sha256: str | None = None, selected: str | None = "OPT-a") -> dict[str, object]:
        request = self.request()
        value: dict[str, object] = {
            "schema_version": "1.0.0",
            "id": response_id,
            "decision_id": "DR001",
            "project_id": "project/probe",
            "run_id": "HR001",
            "task_id": "TASK001",
            "attempt_id": "AT001",
            "request_sha256": request_sha256 or request["request_sha256"],
            "action": "SELECT",
            "selected_option": selected,
            "actor_id": "human-001",
            "reason": "Direction A preserves the current bounded scope.",
            "resolved_at": NOW,
        }
        value["response_sha256"] = canonical_sha256(value)
        return value

    def wait(self) -> dict[str, object]:
        return record_human_required(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id="AT001",
            worker_id="worker-a",
            lease_token=self.lease_token,
            result_request=self.request_input(),
            now=NOW,
        )

    def test_human_required_releases_lease_without_consuming_attempt_or_blocking_dependents(self) -> None:
        result = self.wait()
        replay = self.wait()
        runtime = load_json(self.project / "07_runtime/research-state.json")["task_runtime"]
        task = runtime["tasks"]["TASK001"]
        self.assertEqual("WAITING_HUMAN", task["status"])
        self.assertIsNone(task["lease"])
        self.assertEqual(0, task["attempts"])
        self.assertEqual("PENDING", runtime["tasks"]["TASK002"]["status"])
        self.assertEqual("DR001", result["request"]["id"])
        self.assertTrue(replay["task"]["idempotent"])
        self.assertEqual(["DR001"], [item["id"] for item in unresolved_requests(work_root=self.work, project_id="project/probe")])

    def test_harness_persists_a_human_required_adapter_result(self) -> None:
        result = record_attempt_result(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            project_id="project/probe",
            run_id="HR001",
            task_id="TASK001",
            attempt_id="AT001",
            worker_id="worker-a",
            lease_token=self.lease_token,
            result={"status": "HUMAN_REQUIRED", "human_decision_request": self.request_input()},
            now=NOW,
        )
        self.assertEqual("WAITING_HUMAN", result["task"]["status"])
        self.assertEqual("CENTRAL_PROPOSITION_CHANGE", result["request"]["human_decision_category"])

    def test_resolve_makes_the_same_task_ready_and_context_carries_response_hash(self) -> None:
        self.wait()
        result = resolve_request(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            project_id="project/probe",
            response=self.response(),
        )
        self.assertFalse(result["idempotent"])
        runtime = load_json(self.project / "07_runtime/research-state.json")["task_runtime"]
        self.assertEqual("PENDING", runtime["tasks"]["TASK001"]["status"])
        self.assertEqual(0, runtime["tasks"]["TASK001"]["attempts"])
        self.assertEqual([], unresolved_requests(work_root=self.work, project_id="project/probe"))
        action = build_next_action(
            REPO_ROOT,
            "project/probe",
            "worker-b",
            NOW,
            dry_run=True,
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
        )
        decision = action["context"]["human_decision"]
        self.assertEqual("DR001", decision["request_id"])
        self.assertEqual(result["response"]["response_sha256"], decision["response_sha256"])
        self.assertEqual("SELECT", decision["action"])

    def test_stale_option_replay_and_invalid_category_are_named(self) -> None:
        with self.assertRaisesRegex(HumanDecisionError, "HUMAN-DECISION-CATEGORY"):
            build_request(
                self.request_input(category="UNAPPROVED"),
                project_id="project/probe", run_id="HR001", task_id="TASK001", attempt_id="AT001", created_at=NOW,
            )
        self.wait()
        with self.assertRaisesRegex(HumanDecisionError, "HUMAN-DECISION-STALE"):
            resolve_request(protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe", response=self.response(request_sha256="sha256:" + "0" * 64))
        with self.assertRaisesRegex(HumanDecisionError, "HUMAN-DECISION-OPTION"):
            resolve_request(protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe", response=self.response(selected="OPT-unknown"))
        first = resolve_request(protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe", response=self.response())
        self.assertTrue(resolve_request(protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe", response=first["response"])["idempotent"])
        different = self.response(response_id="DRR002", selected="OPT-b")
        with self.assertRaisesRegex(HumanDecisionError, "HUMAN-DECISION-REPLAY"):
            resolve_request(protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe", response=different)

    def test_request_replay_is_idempotent_and_non_select_option_is_rejected(self) -> None:
        request = self.request()
        self.assertEqual(
            request,
            record_request(protocol_root=REPO_ROOT, work_root=self.work, request=request),
        )
        with self.assertRaisesRegex(HumanDecisionError, "HUMAN-DECISION-OPTION"):
            invalid = self.response()
            invalid["action"] = "APPROVE"
            invalid["response_sha256"] = canonical_sha256({key: value for key, value in invalid.items() if key != "response_sha256"})
            resolve_request(protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe", response=invalid)

    def test_harness_wraps_invalid_human_request_with_named_error(self) -> None:
        with self.assertRaisesRegex(HarnessError, "HUMAN-DECISION-CATEGORY"):
            record_attempt_result(
                protocol_root=REPO_ROOT,
                work_root=self.work,
                project_id="project/probe",
                run_id="HR001",
                task_id="TASK001",
                attempt_id="AT001",
                worker_id="worker-a",
                lease_token=self.lease_token,
                result={"status": "HUMAN_REQUIRED", "human_decision_request": self.request_input(category="UNAPPROVED")},
                now=NOW,
            )

    def test_run_log_replay_reconstructs_waiting_and_ready_states(self) -> None:
        self.wait()
        waiting = task_runtime.replay_runtime(self.work, "project/probe")
        self.assertEqual("WAITING_HUMAN", waiting["tasks"]["TASK001"]["status"])
        resolve_request(protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe", response=self.response())
        ready = task_runtime.replay_runtime(self.work, "project/probe")
        self.assertEqual("PENDING", ready["tasks"]["TASK001"]["status"])
        self.assertEqual("DRR001", ready["tasks"]["TASK001"]["human_decision_response"]["id"])

    def test_decisions_list_and_resolve_cli_are_machine_readable(self) -> None:
        self.wait()
        listed = subprocess.run(
            [sys.executable, "tools/harness.py", "decisions", "list", "project/probe", "--protocol-root", str(REPO_ROOT), "--work-root", str(self.work)],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertEqual("DR001", json.loads(listed.stdout)["requests"][0]["id"])
        response_path = self.root / "response.json"
        response_path.write_text(stable_json(self.response()), encoding="utf-8")
        resolved = subprocess.run(
            [sys.executable, "tools/harness.py", "decisions", "resolve", "project/probe", "--protocol-root", str(REPO_ROOT), "--work-root", str(self.work), "--response", str(response_path)],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertEqual("DRR001", json.loads(resolved.stdout)["response"]["id"])


if __name__ == "__main__":
    unittest.main()
