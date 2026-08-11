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
from private_evidence import PrivateEvidenceAdapter, PrivateEvidenceAdapterError
from validate import validate_repository


class PrivateEvidenceAdapterContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def make_metadata(self, target: str = "project/private-signal") -> dict[str, object]:
        return {
            "source_type": "private-source-metadata",
            "source_location": "gdrive://opaque-source-001",
            "content_hash": "sha256:" + "a" * 64,
            "rights_status": "private-use-only",
            "related_projects": [target],
            "related_questions": ["Q001"],
            "acquired_at": "2026-08-11T16:00:00+09:00",
            "creator": "creator/fixture",
            "created_at": "unknown",
            "observed_by": "private-signal-adapter",
        }

    def make_signal(self) -> dict[str, object]:
        return {
            "statement": "A preference for repeated intervals is suggested.",
            "scope": "creator/fixture",
            "epistemic_status": "SUPPORTED",
            "valid_from": "2026-08-11",
            "review_after": "2027-08-11",
        }

    def add_question(self, project: Path) -> None:
        (project / "01_planning" / "question-register.yaml").write_text(
            yaml.safe_dump(
                {"questions": [{"id": "Q001", "text": "Which preference signal is relevant?", "status": "ANSWERED"}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def test_build_rejects_raw_fields_credentials_and_unapproved_uri(self) -> None:
        root = self.make_root()
        adapter = PrivateEvidenceAdapter(root)
        with self.assertRaisesRegex(PrivateEvidenceAdapterError, "forbidden raw field"):
            adapter.build_records(
                {**self.make_metadata(), "raw_content": "never inspect this"},
                self.make_signal(),
                evidence_id="EV001",
                claim_id="CL001",
            )
        for location in ("file:///tmp/private-note", "gdrive://user:password@opaque-source-001", "gdrive://opaque/../source"):
            with self.subTest(location=location):
                with self.assertRaises(PrivateEvidenceAdapterError):
                    adapter.build_records(
                        {**self.make_metadata(), "source_location": location},
                        self.make_signal(),
                        evidence_id="EV001",
                        claim_id="CL001",
                    )

    def test_import_writes_only_safe_records_and_is_idempotent(self) -> None:
        root = self.make_root()
        project = create_project(root, "private-signal", "Private Signal", "creator/fixture", "2026-08-11T15:00:00+09:00")
        self.add_question(project)
        adapter = PrivateEvidenceAdapter(root)
        first = adapter.import_records("project/private-signal", self.make_metadata(), self.make_signal())
        second = adapter.import_records("project/private-signal", self.make_metadata(), self.make_signal())
        self.assertTrue(first["imported"])
        self.assertFalse(second["imported"])
        evidence = first["evidence"]
        claim = first["claim"]
        self.assertEqual("PRIVATE_DERIVED", evidence["sensitivity"])
        self.assertEqual("PREFERENCE_SIGNAL", claim["type"])
        self.assertEqual([evidence["id"]], claim["evidence_ids"])
        serialized = (project / "02_evidence" / "evidence-ledger.jsonl").read_text(encoding="utf-8")
        serialized += (project / "03_knowledge" / "claims.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("raw_content", serialized)
        self.assertNotIn("never inspect", serialized)
        self.assertEqual(1, len((project / "02_evidence" / "evidence-ledger.jsonl").read_text(encoding="utf-8").splitlines()))
        self.assertEqual(1, len((project / "03_knowledge" / "claims.jsonl").read_text(encoding="utf-8").splitlines()))
        self.assertEqual([], validate_repository(root))

    def test_import_rejects_unapproved_signal_and_cross_project_metadata(self) -> None:
        root = self.make_root()
        project = create_project(root, "private-signal", "Private Signal", "creator/fixture", "2026-08-11T15:00:00+09:00")
        self.add_question(project)
        adapter = PrivateEvidenceAdapter(root)
        with self.assertRaisesRegex(PrivateEvidenceAdapterError, "not approved"):
            adapter.import_records(
                "project/private-signal",
                self.make_metadata(),
                {**self.make_signal(), "epistemic_status": "VERIFIED"},
            )
        with self.assertRaisesRegex(PrivateEvidenceAdapterError, "only to the import target"):
            adapter.import_records(
                "project/private-signal",
                self.make_metadata("project/other-project"),
                self.make_signal(),
            )


if __name__ == "__main__":
    unittest.main()
