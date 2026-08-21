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

from new_project import create_project
from validate import Finding, validate_repository


def _declare_finished(project, status: str) -> None:
    """Put a fixture project into a status that claims the work is over."""
    state_path = project / "07_runtime" / "research-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = status
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path = project / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["project"]["status"] = status
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report_path = project / "07_runtime" / "completion-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = status
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ValidationErrorContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_schema_error_reports_file_field_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "invalid-manifest", "Invalid Manifest")
        manifest_path = project / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["access"]["classification"] = "NOT_A_CLASS"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "SCHEMA:enum")
        self.assertEqual("projects/invalid-manifest/manifest.yaml", finding.path)
        self.assertEqual("access.classification", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_jsonl_parse_error_reports_file_line_field_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "invalid-jsonl", "Invalid JSONL")
        claims_path = project / "03_knowledge" / "claims.jsonl"
        claims_path.write_text(json.dumps({"id": "CL001"}) + "\nnot-json\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "JSONL")
        self.assertEqual("projects/invalid-jsonl/03_knowledge/claims.jsonl", finding.path)
        self.assertEqual(2, finding.line)
        self.assertEqual("$", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_duplicate_yaml_key_reports_file_line_field_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "duplicate-yaml", "Duplicate YAML")
        question_path = project / "01_planning" / "question-register.yaml"
        question_path.write_text("questions: []\nquestions: []\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "YAML")
        self.assertEqual("projects/duplicate-yaml/01_planning/question-register.yaml", finding.path)
        self.assertEqual(2, finding.line)
        self.assertEqual("questions", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_forbidden_filename_fails_repository_boundary(self) -> None:
        root = self.make_root()
        project = create_project(root, "forbidden-name", "Forbidden Name")
        env_path = project / ".env"
        env_path.write_text("TOKEN=synthetic", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.path.endswith("/.env"))
        self.assertEqual("DATA-BOUNDARY", finding.rule)
        self.assertIn("remediation:", finding.render())

    def test_likely_secret_reports_file_line_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "secret-scan", "Secret Scan")
        source_path = project / "02_evidence" / "source.txt"
        source_path.write_text("api_" + "key=" + "a" * 32 + "\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "SECRET-SCAN")
        self.assertEqual("projects/secret-scan/02_evidence/source.txt", finding.path)
        self.assertEqual(1, finding.line)
        self.assertEqual("$", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_private_record_in_approved_snapshot_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "private-snapshot", "Private Snapshot")
        snapshot_path = project / "02_evidence" / "approved-snapshots" / "records.jsonl"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps({"sensitivity": "PRIVATE_RAW"}) + "\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "DATA-BOUNDARY" and item.field == "sensitivity")
        self.assertEqual(1, finding.line)
        self.assertIn("remediation:", finding.render())

    def test_broken_cross_reference_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "broken-reference", "Broken Reference")
        evidence = {
            "id": "EV001",
            "source_type": "primary_public",
            "source_location": "https://example.invalid/source/001",
            "created_at": "unknown",
            "acquired_at": "2026-08-11T15:00:00+09:00",
            "content_hash": "sha256:" + "0" * 64,
            "rights_status": "public-use",
            "sensitivity": "PUBLIC_CITABLE",
            "redistribution": "allowed",
            "related_projects": ["project/broken-reference"],
            "related_questions": ["Q999"],
            "extraction_status": "processed",
            "direct_observation": False,
            "observed_by": "collector",
        }
        (project / "02_evidence" / "evidence-ledger.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "CROSS-REFERENCE")
        self.assertEqual("evidence-ledger.jsonl", Path(finding.path).name)
        self.assertEqual("EV001.related_questions[0]", finding.field)

    def test_mandatory_question_left_open_by_a_finished_project_is_reported(self) -> None:
        """A question is asked before it is answered, so "still open" is only wrong once the project says it is done."""
        root = self.make_root()
        project = create_project(root, "open-question", "Open Question")
        question_path = project / "01_planning" / "question-register.yaml"
        question_path.write_text(
            yaml.safe_dump(
                {"questions": [{"id": "Q001", "text": "Question", "priority": "mandatory", "status": "OPEN"}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        _declare_finished(project, "COMPLETE_WITH_GAPS")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "QUESTION-TERMINAL")
        self.assertEqual("Q001.status", finding.field)

    def test_a_project_still_working_may_hold_an_open_mandatory_question(self) -> None:
        root = self.make_root()
        project = create_project(root, "working-question", "Working Question")
        (project / "01_planning" / "question-register.yaml").write_text(
            yaml.safe_dump(
                {"questions": [{"id": "Q001", "text": "Question", "priority": "mandatory", "status": "OPEN"}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        findings = validate_repository(root)

        self.assertEqual([], [item for item in findings if item.rule == "QUESTION-TERMINAL"])

    def test_illegal_lifecycle_transition_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "illegal-transition", "Illegal Transition")
        run_log = project / "07_runtime" / "run-log.jsonl"
        run_log.write_text(
            json.dumps({"event_id": "EVT001", "event_type": "STATE_TRANSITION", "from_status": "DRAFT", "to_status": "COMPLETE"}) + "\n",
            encoding="utf-8",
        )

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "LIFECYCLE-TRANSITION")
        self.assertEqual(1, finding.line)
        self.assertEqual("from_status/to_status", finding.field)

    def test_mandatory_requirement_needs_resolved_acceptance_test(self) -> None:
        root = self.make_root()
        project = create_project(root, "untestable-requirement", "Untestable Requirement")
        requirements_path = project / "05_production" / "production-requirements.yaml"
        requirements_path.write_text(
            yaml.safe_dump(
                {
                    "requirements": [
                        {
                            "id": "RQ001",
                            "category": "visual",
                            "statement": "A testable visual constraint.",
                            "source_decisions": [],
                            "priority": "mandatory",
                            "acceptance_test_ids": [],
                            "status": "ADOPTED",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "REQUIREMENT-TEST")
        self.assertEqual("RQ001.acceptance_test_ids", finding.field)

    def test_duplicate_canonical_id_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "duplicate-id", "Duplicate ID")
        base = {
            "id": "EV001",
            "source_type": "primary_public",
            "source_location": "https://example.invalid/source/001",
            "created_at": "unknown",
            "acquired_at": "2026-08-11T15:00:00+09:00",
            "content_hash": "sha256:" + "0" * 64,
            "rights_status": "public-use",
            "sensitivity": "PUBLIC_CITABLE",
            "redistribution": "allowed",
            "related_projects": ["project/duplicate-id"],
            "related_questions": [],
            "extraction_status": "processed",
            "direct_observation": False,
            "observed_by": "collector",
        }
        (project / "02_evidence" / "evidence-ledger.jsonl").write_text(
            json.dumps(base) + "\n" + json.dumps(base) + "\n", encoding="utf-8"
        )

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "DUPLICATE-ID")
        self.assertEqual("id", finding.field)
        self.assertIn("EV001", finding.message)


if __name__ == "__main__":
    unittest.main()
