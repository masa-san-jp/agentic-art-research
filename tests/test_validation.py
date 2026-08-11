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


if __name__ == "__main__":
    unittest.main()
