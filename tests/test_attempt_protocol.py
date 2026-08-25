from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    schema = load_json(REPO_ROOT / "schemas" / f"{name}.schema.json")
    common = load_json(REPO_ROOT / "schemas" / "common.schema.json")
    registry = Registry().with_resources([(common["$id"], Resource.from_contents(common))])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


class AttemptProtocolSchemaTest(unittest.TestCase):
    def test_request_fixture_is_valid_and_unknown_top_level_field_is_rejected(self) -> None:
        request = load_json(REPO_ROOT / "tests/fixtures/harness/attempt-request.json")
        request_validator = validator("agent-attempt-request")
        self.assertEqual([], list(request_validator.iter_errors(request)))
        invalid = dict(request)
        invalid["credential"] = "must-not-be-accepted"
        errors = list(request_validator.iter_errors(invalid))
        self.assertTrue(any(error.validator == "additionalProperties" for error in errors))

    def test_result_contract_requires_typed_failure_or_human_decision(self) -> None:
        result = {
            "schema_version": "1.0.0",
            "run_id": "HR001",
            "attempt_id": "AT001",
            "status": "FAILED",
            "summary": "Worker attempt failed.",
            "effect_key": None,
            "outputs": [],
            "failure": {"class": "WORKER-TIMEOUT", "message": "Worker timed out."},
            "human_decision_request": None,
            "diagnostics": {"stderr": "", "exit_code": None, "signal": None, "timed_out": True},
        }
        result_validator = validator("agent-attempt-result")
        self.assertEqual([], list(result_validator.iter_errors(result)))
        invalid = dict(result)
        invalid["failure"] = None
        errors = list(result_validator.iter_errors(invalid))
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
