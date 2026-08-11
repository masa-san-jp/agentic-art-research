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
sys.path.insert(0, str(REPO_ROOT / "tests"))

from context_pack import ContextPackError, build_context_pack, render_context_pack
from new_project import create_project
from test_schemas import validator_for


class ContextPackContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        create_project(temporary, "context-test", "Context Test", created_at="2026-08-11T00:00:00+09:00")
        return temporary

    def write_jsonl(self, path: Path, records: list[dict]) -> None:
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def prepare_task(self, root: Path, *, role: str = "analyst", evidence_ids: list[str] | None = None) -> None:
        project = root / "projects/context-test"
        (project / "01_planning/research-plan.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "tasks": [
                        {
                            "id": "TASK001",
                            "title": "Analyze the selected evidence",
                            "role": role,
                            "depends_on": [],
                            "question_ids": ["Q001"],
                            "evidence_ids": evidence_ids or ["EV001"],
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "01_planning/question-register.yaml").write_text(
            "questions:\n  - id: Q001\n    text: Which interval matters?\n    status: OPEN\n", encoding="utf-8"
        )
        self.write_jsonl(
            project / "02_evidence/evidence-ledger.jsonl",
            [
                {"id": "EV001", "related_questions": ["Q001"], "sensitivity": "PUBLIC_CITABLE"},
                {"id": "EV002", "related_questions": ["Q002"], "sensitivity": "PUBLIC_CITABLE"},
            ],
        )
        self.write_jsonl(project / "03_knowledge/claims.jsonl", [{"id": "CL001", "evidence_ids": ["EV001"]}])

    def test_pack_contains_only_task_relevant_chain_and_is_deterministic(self) -> None:
        root = self.make_root()
        self.prepare_task(root)
        first = build_context_pack(root, "project/context-test", "TASK001")
        second = render_context_pack(root, "project/context-test", "TASK001")
        self.assertEqual(first, json.loads(second))
        self.assertEqual([], list(validator_for("context-pack").iter_errors(first)))
        self.assertEqual("analyst", first["role"]["id"])
        self.assertEqual(["EV001"], [record["id"] for record in first["records"]["evidence"]])
        self.assertEqual(["CL001"], [record["id"] for record in first["records"]["claims"]])
        self.assertNotIn("EV002", second)

    def test_role_boundary_rejects_mismatched_or_overbroad_references(self) -> None:
        root = self.make_root()
        self.prepare_task(root, role="planner", evidence_ids=["EV001"])
        with self.assertRaisesRegex(ContextPackError, "cannot receive evidence"):
            build_context_pack(root, "project/context-test", "TASK001")
        self.prepare_task(root, role="analyst")
        with self.assertRaisesRegex(ContextPackError, "assigned to role"):
            build_context_pack(root, "project/context-test", "TASK001", role="collector")


if __name__ == "__main__":
    unittest.main()
