from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from audit import audit_graph
from build_graph import build_graph
from new_project import create_project


class AuditContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def write_jsonl(self, path: Path, records: list[dict[str, object]]) -> None:
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    def test_audit_reports_non_blocking_quality_findings(self) -> None:
        root = self.make_root()
        project = create_project(root, "audit-test", "Audit Test")
        (project / "01_planning" / "question-register.yaml").write_text(
            yaml.safe_dump({"questions": [{"id": "Q001", "text": "Question", "status": "ANSWERED"}]}, sort_keys=False),
            encoding="utf-8",
        )
        evidence = {
            "id": "EV001",
            "source_type": "primary_public",
            "source_location": "https://example.invalid/source/001",
            "created_at": "unknown",
            "acquired_at": "2020-01-01T00:00:00+00:00",
            "content_hash": "sha256:" + "0" * 64,
            "rights_status": "public-use",
            "sensitivity": "PUBLIC_CITABLE",
            "redistribution": "allowed",
            "related_projects": ["project/audit-test"],
            "related_questions": ["Q001"],
            "extraction_status": "processed",
            "direct_observation": False,
            "observed_by": "fixture",
        }
        evidence2 = dict(evidence, id="EV002", related_questions=[])
        self.write_jsonl(project / "02_evidence" / "evidence-ledger.jsonl", [evidence, evidence2])
        self.write_jsonl(
            project / "03_knowledge" / "claims.jsonl",
            [
                {
                    "id": "CL001",
                    "statement": "Claim",
                    "type": "INFERENCE",
                    "evidence_ids": ["EV001"],
                    "supporting_claims": [],
                    "opposing_claims": [],
                    "scope": "fixture",
                    "epistemic_status": "SUPPORTED",
                    "review_after": "2020-01-01",
                }
            ],
        )
        (project / "04_decisions" / "insight-register.yaml").write_text(
            yaml.safe_dump({"insights": [{"id": "IN001", "claim_ids": ["CL001"], "opposing_claim_ids": []}]}, sort_keys=False),
            encoding="utf-8",
        )
        (project / "04_decisions" / "decision-log.yaml").write_text(
            yaml.safe_dump(
                {
                    "decisions": [
                        {
                            "id": "DC001",
                            "question": "Question",
                            "selected_option": "Option",
                            "rejected_options": [],
                            "insight_ids": ["IN001"],
                            "evidence_ids": [],
                            "reason": "Reason",
                            "authority": "agent-recommended",
                            "status": "PROPOSED",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "05_production" / "production-requirements.yaml").write_text(
            yaml.safe_dump(
                {
                    "requirements": [
                        {
                            "id": "RQ001",
                            "category": "visual",
                            "statement": "Observable constraint",
                            "source_decisions": ["DC001"],
                            "priority": "mandatory",
                            "acceptance_test_ids": ["AT001"],
                            "status": "ADOPTED",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "05_production" / "acceptance-tests.yaml").write_text(
            yaml.safe_dump(
                {"acceptance_tests": [{"id": "AT001", "target_requirement": "RQ001", "result": "NOT_RUN"}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        findings = audit_graph(
            build_graph(root),
            root=root,
            now=datetime(2026, 8, 11, tzinfo=timezone.utc),
        )
        joined = "\n".join(findings)
        for marker in ("BIAS:", "STALE-EVIDENCE:", "NO-COUNTEREVIDENCE:", "WEAK-DECISION:", "UNRUN-TEST:"):
            self.assertIn(marker, joined)

    def test_audit_is_non_blocking_for_a_clean_empty_project(self) -> None:
        root = self.make_root()
        create_project(root, "clean-audit", "Clean Audit")
        self.assertEqual([], audit_graph(build_graph(root), root=root, now=datetime(2026, 8, 11, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
