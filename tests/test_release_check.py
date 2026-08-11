from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "harmony"

import sys

sys.path.insert(0, str(REPO_ROOT / "tools"))

from release_check import RELEASE_SCHEMA, check_release  # noqa: E402


class ReleaseCheckContractTest(unittest.TestCase):
    def test_release_gate_passes_and_report_is_schema_valid(self) -> None:
        result = check_release(REPO_ROOT, FIXTURE, REPO_ROOT / "execution" / "ci-evidence.json")
        self.assertEqual(RELEASE_SCHEMA, result["schema"])
        self.assertTrue(result["passed"])
        schema = json.loads((REPO_ROOT / "schemas" / "release-check.schema.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(result))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_release_gate_requires_three_successful_validate_runs(self) -> None:
        evidence = json.loads((REPO_ROOT / "execution" / "ci-evidence.json").read_text(encoding="utf-8"))
        evidence["runs"] = evidence["runs"][:2]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ci-evidence.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            result = check_release(REPO_ROOT, FIXTURE, path)
        self.assertFalse(result["passed"])
        ci_check = next(check for check in result["checks"] if check["id"] == "ci_three_runs")
        self.assertFalse(ci_check["passed"])


if __name__ == "__main__":
    unittest.main()
