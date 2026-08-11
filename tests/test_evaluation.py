from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "harmony"
sys.path.insert(0, str(REPO_ROOT / "tools"))

from evaluate import evaluate_project, evaluate_offline_fixture
from run_project import run_offline_fixture


class EvaluationContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_offline_fixture_passes_all_gates_deterministically(self) -> None:
        root = self.make_root()
        first = evaluate_offline_fixture(root, FIXTURE)
        second = evaluate_offline_fixture(root, FIXTURE)
        self.assertTrue(first["passed"], json.dumps(first, ensure_ascii=False))
        self.assertEqual(first, second)
        self.assertEqual(
            {"accuracy", "traceability", "termination", "resume", "privacy"},
            set(first["gates"]),
        )
        self.assertEqual(1.0, first["gates"]["traceability"]["score"])

    def test_traceability_and_privacy_gates_fail_on_canonical_regressions(self) -> None:
        root = self.make_root()
        project = run_offline_fixture(root, "harmony-study", FIXTURE)
        requirements_path = project / "05_production" / "production-requirements.yaml"
        requirements_path.write_text(
            requirements_path.read_text(encoding="utf-8").replace("acceptance_test_ids: [AT001]", "acceptance_test_ids: []"),
            encoding="utf-8",
        )
        evidence_path = project / "02_evidence" / "evidence-ledger.jsonl"
        evidence = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
        evidence[0]["sensitivity"] = "PRIVATE_RAW"
        evidence_path.write_text("".join(json.dumps(record) + "\n" for record in evidence), encoding="utf-8")
        result = evaluate_project(root, "project/harmony-study", expected_status="COMPLETE_WITH_GAPS")
        self.assertFalse(result["passed"])
        self.assertFalse(result["gates"]["traceability"]["passed"])
        self.assertFalse(result["gates"]["privacy"]["passed"])

    def test_evaluation_does_not_modify_repository(self) -> None:
        root = self.make_root()
        project = run_offline_fixture(root, "harmony-study", FIXTURE)
        before = {
            path.relative_to(root): path.read_bytes()
            for path in sorted(project.rglob("*"))
            if path.is_file()
        }
        result = evaluate_project(root, "project/harmony-study", expected_status="COMPLETE_WITH_GAPS")
        self.assertTrue(result["passed"])
        after = {
            path.relative_to(root): path.read_bytes()
            for path in sorted(project.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
