from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from _common import stable_json  # noqa: E402
from canonical import canonical_sha256  # noqa: E402
from context_pack import build_context_pack  # noqa: E402
from new_project import create_project  # noqa: E402
from retry_feedback import RetryFeedbackError, build_retry_feedback  # noqa: E402
import task_runtime  # noqa: E402


NOW = "2026-08-25T12:00:00+09:00"


def failed_report() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "report_id": "AR-0123456789abcdef",
        "project_id": "project/probe",
        "run_id": "HR701",
        "task_id": "TASK001",
        "attempt_id": "AT001",
        "evaluated_at": NOW,
        "status": "FAIL",
        "gates": [
            {
                "id": "AG-TEST-VALIDATE",
                "kind": "project_validate",
                "status": "FAIL",
                "path": "01_planning/question-register.yaml",
                "expected": 0,
                "actual": 1,
                "rule": "ACCEPTANCE-GATE-FAILED",
                "remediation": "Correct the invalid question status.",
                "duration_ms": 0,
                "findings": ["question-register.yaml: status NOT_A_STATUS is not allowed"],
            }
        ],
    }


class RetryFeedbackContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.output.mkdir()
        (self.work / "projects").mkdir()
        shutil.copytree(REPO_ROOT / "config", self.work / "config")
        self.project = create_project(self.work, "probe", "Probe", protocol_root=REPO_ROOT)
        task_runtime.initialize_runtime(self.work, "project/probe", initialized_at=NOW)
        self.addCleanup(shutil.rmtree, self.root, True)

    def write_report(self, report: dict[str, object]) -> Path:
        path = self.work / ".harness/attempts/HR701/TASK001/AT001/acceptance-report.json"
        path.parent.mkdir(parents=True)
        path.write_text(stable_json(report), encoding="utf-8")
        claimed = task_runtime.claim_next(self.work, "project/probe", "worker-a", now=NOW)
        assert claimed is not None
        task_runtime.fail(
            self.work,
            "project/probe",
            "TASK001",
            "worker-a",
            claimed["lease_token"],
            "VALIDATION",
            "Typed acceptance gate failed.",
            now=NOW,
            failure_details={"report_id": report["report_id"], "report_sha256": canonical_sha256(report)},
        )
        return path

    def test_initial_attempt_has_no_feedback_and_retry_bundle_is_byte_deterministic(self) -> None:
        self.assertIsNone(build_retry_feedback(
            protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe",
            run_id="HR701", task_id="TASK001", current_attempt_id="AT001",
        ))
        report = failed_report()
        self.write_report(report)
        first = build_retry_feedback(
            protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe",
            run_id="HR701", task_id="TASK001", current_attempt_id="AT002",
        )
        second = build_retry_feedback(
            protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe",
            run_id="HR701", task_id="TASK001", current_attempt_id="AT002",
        )
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual("AT001", first["source_attempt_id"])
        self.assertEqual("AG-TEST-VALIDATE", first["findings"][0]["gate_id"])
        self.assertIn("NOT_A_STATUS", first["findings"][0]["finding"])
        self.assertTrue(first["report_sha256"].startswith("sha256:"))
        self.assertEqual(stable_json(first), stable_json(second))

    def test_context_pack_carries_verified_feedback_only_on_retry(self) -> None:
        self.write_report(failed_report())
        initial = build_context_pack(
            self.work, "project/probe", "TASK001", "planner",
            protocol_root=REPO_ROOT, work_root=self.work, run_id="HR701", attempt_id="AT001",
        )
        retry = build_context_pack(
            self.work, "project/probe", "TASK001", "planner",
            protocol_root=REPO_ROOT, work_root=self.work, run_id="HR701", attempt_id="AT002",
        )
        self.assertNotIn("retry_feedback", initial)
        self.assertIn("retry_feedback", retry)
        self.assertEqual("AT001", retry["retry_feedback"]["source_attempt_id"])
        self.assertEqual("sha256:", retry["retry_feedback"]["report_sha256"][:7])

    def test_tampered_report_identity_is_rejected(self) -> None:
        report = failed_report()
        report["task_id"] = "TASK002"
        self.write_report(report)
        with self.assertRaisesRegex(RetryFeedbackError, "RETRY-FEEDBACK-PROVENANCE"):
            build_retry_feedback(
                protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe",
                run_id="HR701", task_id="TASK001", current_attempt_id="AT002",
            )

    def test_tampered_report_content_is_rejected_against_runtime_hash(self) -> None:
        report = failed_report()
        self.write_report(report)
        path = self.work / ".harness/attempts/HR701/TASK001/AT001/acceptance-report.json"
        report["gates"][0]["findings"][0] = "injected worker instruction"
        path.write_text(stable_json(report), encoding="utf-8")
        with self.assertRaisesRegex(RetryFeedbackError, "RETRY-FEEDBACK-PROVENANCE"):
            build_retry_feedback(
                protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe",
                run_id="HR701", task_id="TASK001", current_attempt_id="AT002",
            )

    def test_findings_are_sorted_and_capped_with_omitted_count(self) -> None:
        report = failed_report()
        report["gates"] = [
            {
                "id": f"AG-TEST-{index:03d}",
                "kind": "project_validate",
                "status": "FAIL",
                "path": "01_planning/question-register.yaml",
                "expected": 0,
                "actual": 1,
                "rule": "ACCEPTANCE-GATE-FAILED",
                "remediation": "Correct the invalid question status.",
                "duration_ms": 0,
                "findings": [f"finding {index:02d}"],
            }
            for index in range(21)
        ]
        self.write_report(report)
        feedback = build_retry_feedback(
            protocol_root=REPO_ROOT, work_root=self.work, project_id="project/probe",
            run_id="HR701", task_id="TASK001", current_attempt_id="AT002",
        )
        assert feedback is not None
        self.assertEqual(20, len(feedback["findings"]))
        self.assertEqual(1, feedback["findings_omitted"])
        self.assertEqual("AG-TEST-000", feedback["findings"][0]["gate_id"])


if __name__ == "__main__":
    unittest.main()
