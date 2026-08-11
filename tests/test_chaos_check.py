from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from chaos_check import run_chaos_suite


class ChaosCheckContractTest(unittest.TestCase):
    def test_all_chaos_scenarios_pass_and_are_deterministic(self) -> None:
        first = run_chaos_suite(REPO_ROOT)
        second = run_chaos_suite(REPO_ROOT)
        self.assertTrue(first["passed"], first)
        self.assertEqual(first, second)
        self.assertEqual(
            {"api-stop", "broken-jsonl", "interrupted-lease", "duplicate-effect"},
            {scenario["id"] for scenario in first["scenarios"]},
        )


if __name__ == "__main__":
    unittest.main()
