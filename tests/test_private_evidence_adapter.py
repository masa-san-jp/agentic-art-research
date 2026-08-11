from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from private_evidence_adapter import (  # noqa: E402
    ADAPTER_SCHEMA,
    AdapterError,
    adapt_private_evidence,
)


class PrivateEvidenceAdapterContractTest(unittest.TestCase):
    def make_record(self) -> dict:
        return {
            "id": "EV901",
            "source_type": "approved-personal-derived",
            "source_location": "gdrive://opaque-evidence-901",
            "created_at": "unknown",
            "acquired_at": "2026-08-11T19:30:00+09:00",
            "content_hash": "sha256:" + "9" * 64,
            "rights_status": "private-use-only",
            "sensitivity": "PRIVATE_DERIVED",
            "redistribution": "prohibited",
            "related_projects": ["project/harmony-study"],
            "related_questions": ["Q001"],
            "extraction_status": "processed",
            "direct_observation": False,
            "approval": {
                "purpose": "artistic-research",
                "operation": "export-signals",
                "approval_ref": "consent://opaque-approval-901",
                "approved_at": "2026-08-11T19:25:00+09:00",
            },
            "signal_source": {
                "schema": "urn:self-model-notes:research-signals:v1",
                "source_repository": "masa-san-jp/self-model-notes",
                "source_commit": "a" * 40,
            },
            "subject": "subject/fixture",
            "approved_derived_signals": [
                {
                    "signal_id": "SIG902",
                    "category": "drawn_toward",
                    "code": "material-experimentation",
                    "certainty": "medium",
                    "evidence_refs": ["EV901"],
                },
                {
                    "signal_id": "SIG901",
                    "category": "protects",
                    "code": "reversible-process",
                    "certainty": "low",
                    "evidence_refs": ["EV901"],
                },
            ],
        }

    def schema_validator(self) -> Draft202012Validator:
        schema = json.loads(
            (REPO_ROOT / "schemas" / "private-evidence-adapter.schema.json").read_text(encoding="utf-8")
        )
        common = json.loads((REPO_ROOT / "schemas" / "common.schema.json").read_text(encoding="utf-8"))
        registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
        validator = Draft202012Validator(schema, registry=registry)
        validator.check_schema(schema)
        return validator

    def test_output_is_schema_valid_normalized_and_raw_free(self) -> None:
        record = self.make_record()
        result = adapt_private_evidence(record)
        self.assertEqual(ADAPTER_SCHEMA, result["schema"])
        self.assertEqual(["SIG901", "SIG902"], [item["signal_id"] for item in result["approved_derived_signals"]])
        self.assertEqual("gdrive://opaque-evidence-901", result["evidence"]["source_location"])
        self.assertEqual("PRIVATE_DERIVED", result["evidence"]["sensitivity"])
        self.assertEqual([], list(self.schema_validator().iter_errors(result)))
        forbidden_keys = {"raw", "raw_text", "body", "content", "transcript", "audio", "attachments", "entities", "claims"}

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        self.assertTrue(forbidden_keys.isdisjoint(set(walk(result))))

    def test_adapter_is_deterministic_and_does_not_mutate_input(self) -> None:
        record = self.make_record()
        original = copy.deepcopy(record)
        first = adapt_private_evidence(record)
        second = adapt_private_evidence(record)
        self.assertEqual(first, second)
        self.assertEqual(original, record)

    def test_raw_or_restricted_classifications_fail_closed(self) -> None:
        for classification in ("PRIVATE_RAW", "RESTRICTED"):
            with self.subTest(classification=classification):
                record = self.make_record()
                record["sensitivity"] = classification
                with self.assertRaisesRegex(AdapterError, "sensitivity"):
                    adapt_private_evidence(record)

    def test_raw_content_unknown_fields_and_unapproved_signals_fail_closed(self) -> None:
        cases = (
            ("raw_text", "record contains unsupported field"),
            ("body", "record contains unsupported field"),
        )
        for field, message in cases:
            with self.subTest(field=field):
                record = self.make_record()
                record[field] = "synthetic-only-placeholder"
                with self.assertRaisesRegex(AdapterError, message):
                    adapt_private_evidence(record)

        record = self.make_record()
        record["approved_derived_signals"][0]["category"] = "raw_voice_refs"
        with self.assertRaisesRegex(AdapterError, "category"):
            adapt_private_evidence(record)

    def test_opaque_uri_hash_approval_and_signal_provenance_are_required(self) -> None:
        cases = (
            ("source_location", "https://example.invalid/private/901", "opaque URI"),
            ("content_hash", "not-a-hash", "content_hash"),
            ("approval", {"purpose": "artistic-research"}, "approval is missing"),
            ("signal_source", {"schema": "urn:test"}, "signal_source is missing"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                record = self.make_record()
                record[field] = value
                with self.assertRaisesRegex(AdapterError, message):
                    adapt_private_evidence(record)


if __name__ == "__main__":
    unittest.main()
