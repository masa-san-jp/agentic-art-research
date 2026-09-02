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
    "observation": {
        "valid": "observation.json",
        "invalid": "observation-missing-evidence.json",
        "rule": "required",
    },
    "relationship": {
        "valid": "relationship.json",
        "invalid": "relationship-missing-rationale.json",
        "rule": "required",
    },
    "contradiction": {
        "valid": "contradiction.json",
        "invalid": "contradiction-resolved-without-resolution.json",
        "rule": "type",
    },
    "external-reference": {
        "valid": "external-reference.json",
        "invalid": "external-reference-missing-evidence.json",
        "rule": "required",
    },
    "aesthetic-signal": {
        "valid": "aesthetic-signal.json",
        "invalid": "aesthetic-signal-missing-review-after.json",
        "rule": "required",
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
    "rejected-option": {
        "valid": "rejected-option.json",
        "invalid": "rejected-option-invalid-id.json",
        "rule": "pattern",
    },
    "uncertainty": {
        "valid": "uncertainty.json",
        "invalid": "uncertainty-invalid-severity.json",
        "rule": "enum",
    },
    "research-signal-export": {
        "valid": "research-signal-export.json",
        "invalid": "research-signal-export-invalid-id.json",
        "rule": "pattern",
    },
    "requirement": {
        "valid": "requirement.json",
        "invalid": "requirement-mandatory-without-test.json",
        "rule": "minItems",
    },
    "production-hypothesis": {
        "valid": "production-hypothesis.json",
        "invalid": "production-hypothesis-without-decision.json",
        "rule": "minItems",
    },
    "hypothesis-comparison": {
        "valid": "hypothesis-comparison.json",
        "invalid": "hypothesis-comparison-single-candidate.json",
        "rule": "minItems",
    },
    "prototype-plan": {
        "valid": "prototype-plan.json",
        "invalid": "prototype-plan-without-tasks.json",
        "rule": "minItems",
    },
    "visual-language": {
        "valid": "visual-language.json",
        "invalid": "visual-language-invalid-medium.json",
        "rule": "oneOf",
    },
    "production-handoff": {
        "valid": "production-handoff.json",
        "invalid": "production-handoff-invalid-commit.json",
        "rule": "pattern",
    },
    "completion-report": {
        "valid": "completion-report.json",
        "invalid": "completion-invalid-status.json",
        "rule": "enum",
    },
    "research-plan": {
        "valid": "research-plan.yaml",
        "invalid": "research-plan-lowered-without-reason.yaml",
        "rule": "additionalProperties",
    },
    "prior-art": {
        "valid": "prior-art.json",
        "invalid": "prior-art-invalid-url.json",
        "rule": "pattern",
    },
    "self-repetition-review": {
        "valid": "self-repetition-review.yaml",
        "invalid": "self-repetition-review-invalid-risk.yaml",
        "rule": "enum",
    },
    "self-repetition-scan": {
        "valid": "self-repetition-scan.json",
        "invalid": "self-repetition-scan-invalid-risk.json",
        "rule": "enum",
    },
    "research-state": {
        "valid": "research-state.json",
        "invalid": "research-state-invalid-task-status.json",
        "rule": "enum",
    },
    "research-request": {
        "valid": "research-request.yaml",
        "invalid": "research-request-unknown-field.yaml",
        "rule": "additionalProperties",
    },
    "research-request-receipt": {
        "valid": "research-request-receipt.json",
        "invalid": "research-request-receipt-invalid-version.json",
        "rule": "const",
    },
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        if path.suffix in {".yaml", ".yml"}:
            return yaml.safe_load(handle)
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

    def test_visual_language_required_and_unknown_fields_are_blocking(self) -> None:
        validator = validator_for("visual-language")
        instance = load_json(FIXTURE_ROOT / "schema-valid" / "visual-language.json")
        missing = dict(instance)
        missing.pop("medium")
        self.assertTrue(any(error.validator == "required" for error in validator.iter_errors(missing)))
        unknown = dict(instance)
        unknown["future_field"] = True
        self.assertTrue(any(error.validator == "additionalProperties" for error in validator.iter_errors(unknown)))

    def test_manifest_modes_preserve_v1_and_require_handoff_entry_points(self) -> None:
        validator = validator_for("project-manifest")
        legacy = load_json(FIXTURE_ROOT / "schema-valid" / "project-manifest.json")
        handoff = load_json(FIXTURE_ROOT / "schema-valid" / "project-manifest-handoff.json")
        incomplete = load_json(
            FIXTURE_ROOT / "schema-invalid" / "project-manifest-handoff-missing-entry-points.json"
        )

        self.assertEqual([], list(validator.iter_errors(legacy)))
        self.assertEqual([], list(validator.iter_errors(handoff)))
        errors = list(validator.iter_errors(incomplete))
        self.assertTrue(any(error.validator == "required" for error in errors), errors)

    def test_extension_vocabularies_match_common_schema(self) -> None:
        common = load_json(SCHEMA_ROOT / "common.schema.json")
        vocab = yaml.safe_load((REPO_ROOT / "config" / "vocabularies.yaml").read_text(encoding="utf-8"))
        pairs = {
            "workflowMode": "workflow_modes",
            "costBand": "cost_bands",
            "durationBand": "duration_bands",
            "dependencyLevel": "dependency_levels",
            "reviewStatus": "review_statuses",
            "hypothesisRecommendation": "hypothesis_recommendations",
            "uncertaintySeverity": "uncertainty_severities",
            "prototypeStatus": "prototype_statuses",
            "handoffSelectionStatus": "handoff_selection_statuses",
            "selectionAuthority": "selection_authorities",
            "handoffStatus": "handoff_statuses",
            "relationshipType": "relationship_types",
            "contradictionStatus": "contradiction_statuses",
            "aestheticSignalStrength": "aesthetic_signal_strengths",
            "aestheticSignalContext": "aesthetic_signal_contexts",
            "mediumType": "medium_types",
            "visualLanguageApplicability": "visual_language_applicabilities",
            "uncertaintyStatus": "uncertainty_statuses",
        }
        for definition, vocabulary in pairs.items():
            with self.subTest(definition=definition, vocabulary=vocabulary):
                self.assertEqual(common["$defs"][definition]["enum"], vocab[vocabulary])

    def test_handoff_contract_represents_external_validation_and_non_blocking_gaps(self) -> None:
        hypothesis = load_json(FIXTURE_ROOT / "schema-valid" / "production-hypothesis.json")
        uncertainty = hypothesis["uncertainties"][0]
        uncertainty["prototype_plan_ids"] = []
        uncertainty["external_validation_reason"] = "The venue must be measured by the production team."
        self.assertEqual([], list(validator_for("production-hypothesis").iter_errors(hypothesis)))

        uncertainty["external_validation_reason"] = None
        self.assertTrue(list(validator_for("production-hypothesis").iter_errors(hypothesis)))

        handoff = load_json(FIXTURE_ROOT / "schema-valid" / "production-handoff.json")
        handoff["prototype_plan_ids"] = []
        self.assertEqual([], list(validator_for("production-handoff").iter_errors(handoff)))


if __name__ == "__main__":
    unittest.main()
