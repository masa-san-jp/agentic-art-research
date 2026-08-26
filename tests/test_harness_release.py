from __future__ import annotations

import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from harness_evaluate import evaluate_scenarios  # noqa: E402
from release_check import _harness_checks  # noqa: E402


class HarnessReleaseContractTest(unittest.TestCase):
    def test_reference_matrix_passes_and_is_machine_readable(self) -> None:
        report = evaluate_scenarios(REPO_ROOT, REPO_ROOT / "tests/fixtures/harness/scenarios.yaml")
        self.assertTrue(report["passed"], report)
        self.assertEqual(11, report["scenario_count"])
        self.assertEqual({"success", "worker-crash-once", "timeout", "rate-limit-then-pass", "unauthorized-write", "acceptance-fail", "human-pause-resume", "kill-each-phase", "output-conflict", "secret-output", "concurrent-supervisor"}, {row["id"] for row in report["scenarios"]})

    def test_release_check_exposes_all_harness_gates(self) -> None:
        checks = _harness_checks(REPO_ROOT)
        self.assertEqual(
            {"harness_e2e", "harness_fault_recovery", "harness_output_boundary", "harness_observability"},
            {check["id"] for check in checks},
        )
        self.assertTrue(all(check["passed"] for check in checks), checks)


if __name__ == "__main__":
    unittest.main()
