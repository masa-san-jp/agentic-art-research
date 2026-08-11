from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from new_project import create_project
from test_schemas import load_json, validator_for
from validate import validate_repository


MATRIX_PATH = FIXTURE_ROOT / "validation-matrix.yaml"


class ValidationFixtureMatrixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))

    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def make_project(self, root: Path) -> Path:
        return create_project(root, "matrix-project", "Validation Matrix")

    def write_json(self, path: Path, value: object) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def write_jsonl(self, path: Path, records: list[dict[str, object]]) -> None:
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    def apply_setup(self, project: Path, setup: str) -> None:
        if setup == "malformed-jsonl":
            (project / "03_knowledge" / "claims.jsonl").write_text(
                '{"id": "CL001"}\nnot-json\n', encoding="utf-8"
            )
        elif setup == "duplicate-yaml":
            (project / "01_planning" / "question-register.yaml").write_text(
                "questions: []\nquestions: []\n", encoding="utf-8"
            )
        elif setup == "forbidden-filename":
            (project / ".env").write_text("synthetic", encoding="utf-8")
        elif setup == "private-snapshot":
            snapshot = project / "02_evidence" / "approved-snapshots" / "records.jsonl"
            snapshot.parent.mkdir(parents=True)
            self.write_jsonl(snapshot, [{"sensitivity": "PRIVATE_RAW"}])
        elif setup == "likely-secret":
            (project / "02_evidence" / "source.txt").write_text(
                "api_" + "key=" + "a" * 32 + "\n", encoding="utf-8"
            )
        elif setup == "missing-required-file":
            (project / "03_knowledge" / "observations.jsonl").unlink()
        elif setup == "wrong-project-id":
            path = project / "manifest.yaml"
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            manifest["project"]["id"] = "project/not-the-directory"
            path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        elif setup == "invalid-project-status":
            path = project / "manifest.yaml"
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            manifest["project"]["status"] = "UNKNOWN"
            path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        elif setup == "runtime-manifest-mismatch":
            path = project / "07_runtime" / "research-state.json"
            state = load_json(path)
            state["status"] = "PLANNED"
            self.write_json(path, state)
        elif setup == "broken-reference":
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
                "related_projects": ["project/matrix-project"],
                "related_questions": ["Q999"],
                "extraction_status": "processed",
                "direct_observation": False,
                "observed_by": "fixture",
            }
            self.write_jsonl(project / "02_evidence" / "evidence-ledger.jsonl", [evidence])
        elif setup == "duplicate-canonical-id":
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
                "related_projects": ["project/matrix-project"],
                "related_questions": [],
                "extraction_status": "processed",
                "direct_observation": False,
                "observed_by": "fixture",
            }
            self.write_jsonl(project / "02_evidence" / "evidence-ledger.jsonl", [evidence, evidence])
        elif setup == "invalid-question-status":
            path = project / "01_planning" / "question-register.yaml"
            path.write_text(
                yaml.safe_dump(
                    {"questions": [{"id": "Q001", "text": "Question", "priority": "preferred", "status": "UNKNOWN"}]},
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        elif setup == "open-mandatory-question":
            path = project / "01_planning" / "question-register.yaml"
            path.write_text(
                yaml.safe_dump(
                    {"questions": [{"id": "Q001", "text": "Question", "priority": "mandatory", "status": "OPEN"}]},
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        elif setup == "claim-cycle":
            claims = [
                {
                    "id": "CL001",
                    "statement": "First claim",
                    "type": "INFERENCE",
                    "evidence_ids": [],
                    "supporting_claims": ["CL002"],
                    "opposing_claims": [],
                    "scope": "fixture",
                    "epistemic_status": "HYPOTHESIS",
                },
                {
                    "id": "CL002",
                    "statement": "Second claim",
                    "type": "INFERENCE",
                    "evidence_ids": [],
                    "supporting_claims": ["CL001"],
                    "opposing_claims": [],
                    "scope": "fixture",
                    "epistemic_status": "HYPOTHESIS",
                },
            ]
            self.write_jsonl(project / "03_knowledge" / "claims.jsonl", claims)
        elif setup == "untestable-mandatory-requirement":
            path = project / "05_production" / "production-requirements.yaml"
            path.write_text(
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
        elif setup == "invalid-runtime-status":
            path = project / "07_runtime" / "research-state.json"
            state = load_json(path)
            state["status"] = "UNKNOWN"
            self.write_json(path, state)
        elif setup == "terminal-report-mismatch":
            manifest_path = project / "manifest.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest["project"]["status"] = "COMPLETE"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            state_path = project / "07_runtime" / "research-state.json"
            state = load_json(state_path)
            state["status"] = "COMPLETE"
            self.write_json(state_path, state)
        elif setup == "duplicate-event-id":
            self.write_jsonl(
                project / "07_runtime" / "run-log.jsonl",
                [
                    {"event_id": "EVT001", "event_type": "TASK_STARTED"},
                    {"event_id": "EVT001", "event_type": "TASK_FINISHED"},
                ],
            )
        elif setup == "illegal-transition":
            self.write_jsonl(
                project / "07_runtime" / "run-log.jsonl",
                [{"event_id": "EVT001", "event_type": "STATE_TRANSITION", "from_status": "DRAFT", "to_status": "COMPLETE"}],
            )
        elif setup == "discontinuous-transitions":
            self.write_jsonl(
                project / "07_runtime" / "run-log.jsonl",
                [
                    {"event_id": "EVT001", "event_type": "STATE_TRANSITION", "from_status": "DRAFT", "to_status": "PLANNED"},
                    {"event_id": "EVT002", "event_type": "STATE_TRANSITION", "from_status": "DRAFT", "to_status": "BLOCKED"},
                ],
            )
        elif setup == "invalid-yaml-collection":
            (project / "04_decisions" / "insight-register.yaml").write_text(
                "insights: {}\n", encoding="utf-8"
            )
        elif setup == "malformed-json":
            (project / "07_runtime" / "research-state.json").write_text("{not-json\n", encoding="utf-8")
        elif setup == "malformed-schema-json":
            (project.parent.parent / "schemas" / "claim.schema.json").write_text("{not-json\n", encoding="utf-8")
        elif setup == "missing-schema-meta":
            schema_path = project.parent.parent / "schemas" / "claim.schema.json"
            schema = load_json(schema_path)
            schema.pop("$schema")
            self.write_json(schema_path, schema)
        elif setup == "invalid-schema-definition":
            schema_path = project.parent.parent / "schemas" / "claim.schema.json"
            schema = load_json(schema_path)
            schema["type"] = "not-a-json-schema-type"
            self.write_json(schema_path, schema)
        elif setup == "missing-domain-schema":
            (project.parent.parent / "schemas" / "claim.schema.json").unlink()
        elif setup == "invalid-secret-pattern":
            access_path = project.parent.parent / "config" / "access-policy.yaml"
            access = yaml.safe_load(access_path.read_text(encoding="utf-8"))
            access["secret_patterns"] = [{"id": "broken-pattern", "pattern": "["}]
            access_path.write_text(yaml.safe_dump(access, sort_keys=False), encoding="utf-8")
        else:
            self.fail(f"unknown validation fixture setup: {setup}")

    def test_normal_fixtures_validate(self) -> None:
        normal = self.matrix["normal"]
        self.assertGreaterEqual(len(normal), 2)
        for case in normal:
            with self.subTest(fixture=case["id"]):
                if case["kind"] == "schema":
                    instance = load_json(FIXTURE_ROOT / case["fixture"])
                    errors = list(validator_for(case["schema"]).iter_errors(instance))
                    self.assertEqual([], errors, "\n".join(error.message for error in errors))
                else:
                    root = self.make_root()
                    self.make_project(root)
                    self.assertEqual([], validate_repository(root))

    def test_failure_fixtures_cover_and_report_the_declared_rule(self) -> None:
        failures = self.matrix["failure"]
        declared_rules = {case["rule"] for case in failures}
        self.assertLess(len(declared_rules), len(failures))
        self.assertIn("DATA-BOUNDARY", declared_rules)
        self.assertIn("SCHEMA:required", declared_rules)
        for case in failures:
            with self.subTest(fixture=case["id"]):
                if case["kind"] == "schema":
                    instance = load_json(FIXTURE_ROOT / case["fixture"])
                    errors = list(validator_for(case["schema"]).iter_errors(instance))
                    actual_rules = {f"SCHEMA:{error.validator}" for error in errors}
                else:
                    root = self.make_root()
                    project = self.make_project(root)
                    self.apply_setup(project, case["setup"])
                    actual_rules = {finding.rule for finding in validate_repository(root)}
                self.assertIn(case["rule"], actual_rules, sorted(actual_rules))

    def test_matrix_has_one_entry_for_each_blocking_rule(self) -> None:
        expected = {
            "SCHEMA:required",
            "SCHEMA:pattern",
            "SCHEMA:enum",
            "SCHEMA:minItems",
            "SCHEMA:anyOf",
            "JSONL",
            "YAML",
            "DATA-BOUNDARY",
            "SECRET-SCAN",
            "PROJECT-STRUCTURE",
            "PROJECT-ID",
            "PROJECT-STATUS",
            "STATE-SYNC",
            "CROSS-REFERENCE",
            "DUPLICATE-ID",
            "QUESTION-STATUS",
            "QUESTION-TERMINAL",
            "DEPENDENCY-CYCLE",
            "REQUIREMENT-TEST",
            "LIFECYCLE-STATUS",
            "LIFECYCLE-STATE-SYNC",
            "DUPLICATE-EVENT-ID",
            "LIFECYCLE-TRANSITION",
            "LIFECYCLE-SEQUENCE",
            "STRUCTURE:type",
            "JSON",
            "SCHEMA-JSON",
            "SCHEMA-META",
            "SCHEMA-DEFINITION",
            "SCHEMA-MISSING",
            "SECURITY-PATTERN",
        }
        actual = {case["rule"] for case in self.matrix["failure"]}
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
