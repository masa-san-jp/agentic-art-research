from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "harmony"
sys.path.insert(0, str(REPO_ROOT / "tools"))

from new_project import create_project
from validate import validate_repository


class VisualLanguageValidationTest(unittest.TestCase):
    def make_root(self) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, root / name)
        (root / "projects").mkdir()
        (root / "data").mkdir()
        return root

    def make_project(self, root: Path, *, ready: bool = False) -> Path:
        project = create_project(root, "visual-fixture", "Visual Fixture", "creator/fixture", "2026-08-11T00:00:00+09:00")
        for source in sorted(FIXTURE_ROOT.rglob("*")):
            relative = source.relative_to(FIXTURE_ROOT)
            if source.is_dir() or relative == Path("manifest.yaml"):
                continue
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        for relative in (
            Path("02_evidence/evidence-ledger.jsonl"),
            Path("03_knowledge/claims.jsonl"),
            Path("03_knowledge/observations.jsonl"),
            Path("03_knowledge/relationships.jsonl"),
            Path("03_knowledge/contradictions.jsonl"),
            Path("03_knowledge/external-references.jsonl"),
        ):
            path = project / relative
            if path.exists():
                path.write_text(path.read_text(encoding="utf-8").replace("project/harmony-study", "project/visual-fixture"), encoding="utf-8")
        if ready:
            manifest_path = project / "manifest.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest["project"]["status"] = "READY_FOR_PRODUCTION"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
            state_path = project / "07_runtime/research-state.json"
            state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
            state["status"] = "READY_FOR_PRODUCTION"
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            (project / "07_runtime/run-log.jsonl").write_text("", encoding="utf-8")
        return project

    def rules_for(self, mutate, *, ready: bool = True) -> set[str]:
        root = self.make_root()
        project = self.make_project(root, ready=ready)
        mutate(project)
        return {finding.rule for finding in validate_repository(root)}

    def test_draft_empty_skeleton_is_valid(self) -> None:
        root = self.make_root()
        self.assertEqual([], validate_repository(root))
        project = create_project(root, "draft-visual", "Draft Visual")
        self.assertEqual([], validate_repository(root))
        self.assertEqual("1.1.0", yaml.safe_load((project / "05_production/visual-language.yaml").read_text(encoding="utf-8"))["schema_version"])

    def test_ready_visual_language_is_valid(self) -> None:
        root = self.make_root()
        self.make_project(root, ready=True)
        self.assertEqual([], validate_repository(root))

    def test_zero_medium_decisions_is_named(self) -> None:
        rules = self.rules_for(lambda project: (project / "04_decisions/decision-log.yaml").write_text("decisions: []\n", encoding="utf-8"))
        self.assertIn("VISUAL-LANGUAGE-MEDIUM-DECISION", rules)

    def test_multiple_medium_decisions_is_named(self) -> None:
        def mutate(project: Path) -> None:
            path = project / "04_decisions/decision-log.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["decisions"].append({"id": "DC003", "question": "Which medium should carry the work?", "selected_option": "sculpture", "rejected_options": ["installation"], "insight_ids": ["IN001"], "evidence_ids": ["EV001"], "reason": "A second candidate is intentionally invalid for this contract test.", "authority": "agent-recommended", "status": "ADOPTED"})
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")

        self.assertIn("VISUAL-LANGUAGE-MEDIUM-DECISION", self.rules_for(mutate))

    def test_non_adopted_medium_decision_is_named(self) -> None:
        def mutate(project: Path) -> None:
            path = project / "04_decisions/decision-log.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["decisions"][1]["status"] = "PROPOSED"
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")

        self.assertIn("VISUAL-LANGUAGE-MEDIUM-DECISION", self.rules_for(mutate))

    def test_unresolved_decision_and_duplicate_expression_are_named(self) -> None:
        def mutate(project: Path) -> None:
            path = project / "05_production/visual-language.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["techniques"][0]["source_decision_ids"] = ["DC999"]
            document["palette"]["prohibited"] = [document["palette"]["preferred"][0]]
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")

        rules = self.rules_for(mutate)
        self.assertIn("VISUAL-LANGUAGE-REFERENCE", rules)
        self.assertIn("VISUAL-LANGUAGE-DUPLICATE", rules)

    def test_ready_empty_sections_are_named(self) -> None:
        def mutate(project: Path) -> None:
            path = project / "05_production/visual-language.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["techniques"] = []
            document["prohibited_expressions"] = []
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")

        rules = self.rules_for(mutate)
        self.assertIn("VISUAL-LANGUAGE-COMPLETENESS", rules)

    def test_v11_mechanism_requires_component_and_reference_grounding(self) -> None:
        def mutate(project: Path) -> None:
            path = project / "05_production/visual-language.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            technique = document["techniques"][0]
            technique.pop("proposition_component_ids")
            technique["reference_ids"] = ["XR999"]
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")

        rules = self.rules_for(mutate)
        self.assertIn("VISUAL-LANGUAGE-MECHANISM-GROUNDING", rules)

    def test_legacy_v10_mechanism_remains_readable_during_migration(self) -> None:
        def mutate(project: Path) -> None:
            path = project / "05_production/visual-language.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["schema_version"] = "1.0.0"
            technique = document["techniques"][0]
            for field in ("mechanism", "proposition_component_ids", "reference_ids"):
                technique.pop(field, None)
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")

        self.assertNotIn("VISUAL-LANGUAGE-MECHANISM-GROUNDING", self.rules_for(mutate))


if __name__ == "__main__":
    unittest.main()
