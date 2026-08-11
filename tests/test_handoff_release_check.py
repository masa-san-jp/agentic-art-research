from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from handoff_release_check import CHECK_SCHEMA, check_handoff_release  # noqa: E402


class HandoffReleaseCheckContractTest(unittest.TestCase):
    def test_research_gate_passes_while_external_schema_is_explicitly_pending(self) -> None:
        result = check_handoff_release(
            REPO_ROOT,
            REPO_ROOT / "tests" / "fixtures" / "harmony",
            REPO_ROOT / "tests" / "fixtures" / "harmony-handoff",
            REPO_ROOT / "execution" / "ci-evidence.json",
        )

        self.assertEqual(CHECK_SCHEMA, result["schema"])
        self.assertTrue(result["passed"])
        self.assertFalse(result["schema_snapshot_ready"])
        self.assertEqual(
            "PENDING_EXTERNAL_SCHEMA",
            next(item for item in result["checks"] if item["id"] == "production_result_schema_snapshot")["status"],
        )
        schema = json.loads((REPO_ROOT / "schemas" / "handoff-release-check.schema.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(result))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
