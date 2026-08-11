from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "harmony"
sys.path.insert(0, str(REPO_ROOT / "tools"))

from evaluate import EVALUATION_SCHEMA, EvaluationError, evaluate_offline_fixture  # noqa: E402


class EvaluationContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_offline_evaluation_passes_all_quality_and_safety_gates(self) -> None:
        result = evaluate_offline_fixture(self.make_root(), FIXTURE)
        self.assertEqual(EVALUATION_SCHEMA, result["schema"])
        self.assertTrue(result["passed"])
        self.assertEqual(
            {"accuracy", "traceability", "termination", "resume", "privacy", "audit"},
            {check["id"] for check in result["checks"]},
        )
        self.assertTrue(all(check["passed"] for check in result["checks"]))
        schema = json.loads((REPO_ROOT / "schemas" / "evaluation.schema.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(result))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_evaluation_does_not_overwrite_an_existing_target(self) -> None:
        root = self.make_root()
        (root / "projects" / "harmony-study").mkdir()
        with self.assertRaisesRegex(EvaluationError, "already exists"):
            evaluate_offline_fixture(root, FIXTURE)


if __name__ == "__main__":
    unittest.main()
