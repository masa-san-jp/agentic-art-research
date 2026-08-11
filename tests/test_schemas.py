from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "schemas"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(REPO_ROOT / "tools"))

from new_project import create_project


SCHEMA_CASES = {
    "project-manifest": {
        "valid": "project-manifest.json",
        "invalid": "project-manifest-missing-access.json",
        "rule": "required",
    },
    "evidence": {
        "valid": "evidence.json",
        "invalid": "evidence-invalid-hash.json",
        "rule": "pattern",
    },
    "claim": {
        "valid": "claim.json",
        "invalid": "claim-invalid-type.json",
        "rule": "enum",
    },
    "insight": {
        "valid": "insight.json",
        "invalid": "insight-empty-claims.json",
        "rule": "minItems",
    },
    "decision": {
        "valid": "decision.json",
        "invalid": "decision-without-basis.json",
        "rule": "anyOf",
    },
    "requirement": {
        "valid": "requirement.json",
        "invalid": "requirement-mandatory-without-test.json",
        "rule": "minItems",
    },
    "completion-report": {
        "valid": "completion-report.json",
        "invalid": "completion-invalid-status.json",
        "rule": "enum",
    },
    "research-state": {
        "valid": "research-state.json",
        "invalid": "research-state-invalid-task-status.json",
        "rule": "enum",
    },
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validator_for(schema_name: str) -> Draft202012Validator:
    schema = load_json(SCHEMA_ROOT / f"{schema_name}.schema.json")
    common = load_json(SCHEMA_ROOT / "common.schema.json")
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    validator = Draft202012Validator(schema, registry=registry)
    validator.check_schema(schema)
    return validator


class SchemaContractTest(unittest.TestCase):
    def test_all_domain_schemas_are_draft_2020_12_and_self_consistent(self) -> None:
        for schema_name in SCHEMA_CASES:
            with self.subTest(schema=schema_name):
                schema = load_json(SCHEMA_ROOT / f"{schema_name}.schema.json")
                self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
                validator_for(schema_name)

    def test_valid_fixtures_validate(self) -> None:
        for schema_name, case in SCHEMA_CASES.items():
            with self.subTest(schema=schema_name):
                instance = load_json(FIXTURE_ROOT / "schema-valid" / case["valid"])
                errors = list(validator_for(schema_name).iter_errors(instance))
                self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_invalid_fixtures_fail_at_the_named_rule(self) -> None:
        for schema_name, case in SCHEMA_CASES.items():
            with self.subTest(schema=schema_name):
                instance = load_json(FIXTURE_ROOT / "schema-invalid" / case["invalid"])
                errors = list(validator_for(schema_name).iter_errors(instance))
                self.assertTrue(errors)
                self.assertTrue(
                    any(error.validator == case["rule"] for error in errors),
                    [(error.validator, error.message) for error in errors],
                )

    def test_project_template_json_contracts_validate_after_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(REPO_ROOT / "templates", root / "templates")
            (root / "projects").mkdir()
            project = create_project(root, "schema-template", "Schema Template")

            manifest = yaml.safe_load((project / "manifest.yaml").read_text(encoding="utf-8"))
            completion_report = load_json(project / "07_runtime" / "completion-report.json")
            manifest_errors = list(validator_for("project-manifest").iter_errors(manifest))
            completion_errors = list(validator_for("completion-report").iter_errors(completion_report))
            self.assertEqual([], manifest_errors, "\n".join(error.message for error in manifest_errors))
            self.assertEqual([], completion_errors, "\n".join(error.message for error in completion_errors))


if __name__ == "__main__":
    unittest.main()
