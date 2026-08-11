from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from audit import audit_graph
from build_graph import build_graph
from impact import downstream
from new_project import create_project
from validate import validate_repository


class BootstrapTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_repository_bootstrap_validates(self) -> None:
        findings = validate_repository(REPO_ROOT)
        self.assertEqual([], findings, "\n".join(item.render() for item in findings))

    def test_new_project_is_complete_and_non_destructive(self) -> None:
        root = self.make_root()
        project = create_project(root, "harmony-study", "Harmony Study", "creator/test")
        self.assertTrue((project / "manifest.yaml").exists())
        self.assertTrue((project / "02_evidence" / "evidence-ledger.jsonl").exists())
        self.assertEqual([], validate_repository(root))
        with self.assertRaises(FileExistsError):
            create_project(root, "harmony-study", "Second Title")

    def test_forbidden_raw_file_fails_validation(self) -> None:
        root = self.make_root()
        project = create_project(root, "privacy-test", "Privacy Test")
        forbidden = project / "02_evidence" / "mail.eml"
        forbidden.write_text("synthetic", encoding="utf-8")
        findings = validate_repository(root)
        self.assertTrue(any(item.rule == "DATA-BOUNDARY" for item in findings))

    def test_traceability_graph_and_impact(self) -> None:
        root = self.make_root()
        project = create_project(root, "trace-test", "Trace Test")
        (project / "01_planning" / "question-register.yaml").write_text(
            yaml.safe_dump({"questions": [{"id": "Q001", "text": "Question", "status": "ANSWERED"}]}, sort_keys=False), encoding="utf-8"
        )
        evidence = {
            "id": "EV001",
            "related_questions": ["Q001"],
        }
        (project / "02_evidence" / "evidence-ledger.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        claim = {"id": "CL001", "evidence_ids": ["EV001"], "supporting_claims": [], "opposing_claims": []}
        (project / "03_knowledge" / "claims.jsonl").write_text(json.dumps(claim) + "\n", encoding="utf-8")
        (project / "04_decisions" / "insight-register.yaml").write_text(
            yaml.safe_dump({"insights": [{"id": "IN001", "claim_ids": ["CL001"], "opposing_claim_ids": []}]}, sort_keys=False), encoding="utf-8"
        )
        (project / "04_decisions" / "decision-log.yaml").write_text(
            yaml.safe_dump({"decisions": [{"id": "DC001", "insight_ids": ["IN001"], "evidence_ids": []}]}, sort_keys=False), encoding="utf-8"
        )
        (project / "05_production" / "production-requirements.yaml").write_text(
            yaml.safe_dump({"requirements": [{"id": "RQ001", "source_decisions": ["DC001"]}]}, sort_keys=False), encoding="utf-8"
        )
        (project / "05_production" / "acceptance-tests.yaml").write_text(
            yaml.safe_dump({"acceptance_tests": [{"id": "AT001", "target_requirement": "RQ001"}]}, sort_keys=False), encoding="utf-8"
        )

        graph = build_graph(root)
        impacted = {item["id"] for item in downstream(graph, "EV001")}
        self.assertEqual({"CL001", "IN001", "DC001", "RQ001", "AT001"}, impacted)
        self.assertEqual([], audit_graph(graph))


if __name__ == "__main__":
    unittest.main()

