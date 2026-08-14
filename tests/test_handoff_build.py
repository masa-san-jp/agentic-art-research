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
sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_graph import build_graph
from build_handoff import HandoffBuildError, build_handoff
from bundle import build_bundle
from export_handoff import HandoffExportError, export_handoff
from handoff_common import HandoffInputError, resolve_project
from impact import impact_report
from new_project import create_project


class HandoffBuildContractTest(unittest.TestCase):
    commit = "0123456789abcdef0123456789abcdef01234567"
    generated_at = "2026-08-11T15:00:00+09:00"

    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def make_project(self) -> tuple[Path, Path]:
        root = self.make_root()
        project = create_project(root, "handoff-build", "Handoff Build", "creator/test", self.generated_at)
        fixture = FIXTURE_ROOT / "harmony"
        for source in sorted(fixture.rglob("*")):
            relative = source.relative_to(fixture)
            if relative in {Path("metadata.yaml"), Path("manifest.yaml")} or source.is_dir():
                continue
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        evidence_path = project / "02_evidence" / "evidence-ledger.jsonl"
        evidence_path.write_text(
            evidence_path.read_text(encoding="utf-8").replace("project/harmony-study", "project/handoff-build"),
            encoding="utf-8",
        )

        manifest_path = project / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["workflow_mode"] = "PRODUCTION_HANDOFF"
        manifest["project"].update(
            {
                "status": "COMPLETE_WITH_GAPS",
                "version": "0.1.0",
                "updated_at": self.generated_at,
            }
        )
        manifest["entry_points"].update(
            {
                "production_hypotheses": "04_decisions/production-hypotheses.yaml",
                "hypothesis_comparison": "04_decisions/hypothesis-comparison.yaml",
                "prototype_plans": "05_production/prototype-plans.yaml",
                "production_handoff": "05_production/production-handoff.yaml",
                "production_change_requests": "06_governance/production-change-requests.yaml",
                "production_feedback_imports": "07_runtime/production-feedback-imports.jsonl",
            }
        )
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")

        hypothesis = json.loads((FIXTURE_ROOT / "schema-valid" / "production-hypothesis.json").read_text(encoding="utf-8"))
        hypothesis["single_hypothesis_rationale"] = "Only one candidate preserves the adopted perceptual decision without weakening the intended experience."
        plan = json.loads((FIXTURE_ROOT / "schema-valid" / "prototype-plan.json").read_text(encoding="utf-8"))
        (project / "04_decisions" / "production-hypotheses.yaml").write_text(
            yaml.safe_dump({"hypotheses": [hypothesis]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        (project / "04_decisions" / "hypothesis-comparison.yaml").write_text(
            yaml.safe_dump({"comparisons": []}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        (project / "05_production" / "prototype-plans.yaml").write_text(
            yaml.safe_dump({"prototype_plans": [plan]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return root, project

    def test_build_is_byte_identical_and_rejects_in_place_semantic_change(self) -> None:
        root, project = self.make_project()
        path = build_handoff(root, "project/handoff-build", generated_at=self.generated_at, research_commit=self.commit)
        first = path.read_bytes()
        self.assertEqual(path.resolve(), (project / "05_production" / "production-handoff.yaml").resolve())

        build_handoff(root, "project/handoff-build", research_commit=self.commit)
        self.assertEqual(first, path.read_bytes())

        requirements_path = project / "05_production" / "production-requirements.yaml"
        requirements = yaml.safe_load(requirements_path.read_text(encoding="utf-8"))
        requirements["requirements"][0]["statement"] += " under fixed lighting."
        requirements_path.write_text(yaml.safe_dump(requirements, sort_keys=False, allow_unicode=True), encoding="utf-8")
        with self.assertRaisesRegex(HandoffBuildError, "change in place"):
            build_handoff(root, "project/handoff-build", research_commit=self.commit)
        self.assertEqual(first, path.read_bytes())

        build_handoff(
            root,
            "project/handoff-build",
            research_commit=self.commit,
            handoff_id="HO002",
            revision=2,
            supersedes="HO001",
        )
        updated = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual("HO002", updated["handoff_id"])
        self.assertEqual("HO001", updated["supersedes"])

    def test_export_is_self_contained_and_destination_is_not_silently_overwritten(self) -> None:
        root, _ = self.make_project()
        build_handoff(root, "project/handoff-build", generated_at=self.generated_at, research_commit=self.commit)
        output = root / "data" / "handoffs" / "handoff-build"
        export_handoff(root, "project/handoff-build", output, allow_dirty=True)
        first = {path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        self.assertIn("manifest.yaml", first)
        self.assertIn("provenance.yaml", first)
        self.assertIn("artifacts/source-ref-index.yaml", first)
        self.assertIn("schemas/production-handoff.schema.json", first)
        self.assertNotIn("artifacts/evidence-ledger.jsonl", first)
        self.assertNotIn(b"source_location", first["artifacts/source-ref-index.yaml"])
        self.assertFalse(any(b"/Users/" in content or b"/private/" in content for content in first.values()))

        export_handoff(root, "project/handoff-build", output, allow_dirty=True)
        second = {path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        self.assertEqual(first, second)

        (output / "unexpected.txt").write_text("do not overwrite", encoding="utf-8")
        with self.assertRaisesRegex(HandoffExportError, "different bytes"):
            export_handoff(root, "project/handoff-build", output, allow_dirty=True)

    def test_export_source_ref_index_uses_contract_keys_and_canonical_hashes(self) -> None:
        root, _ = self.make_project()
        build_handoff(root, "project/handoff-build", generated_at=self.generated_at, research_commit=self.commit)
        output = root / "data" / "handoffs" / "handoff-build"
        export_handoff(root, "project/handoff-build", output, allow_dirty=True)
        index = yaml.safe_load((output / "artifacts/source-ref-index.yaml").read_text(encoding="utf-8"))
        self.assertIn("references", index)
        self.assertNotIn("records", index)
        self.assertTrue(index["references"])
        for record in index["references"]:
            self.assertRegex(record["record_hash"], r"^sha256:[0-9a-f]{64}$")
            self.assertNotEqual(record["record_hash"], "sha256:" + "0" * 64)

    def test_force_only_replaces_a_generated_bundle(self) -> None:
        root, _ = self.make_project()
        build_handoff(root, "project/handoff-build", generated_at=self.generated_at, research_commit=self.commit)
        output = root / "data" / "handoffs" / "handoff-build"
        export_handoff(root, "project/handoff-build", output, allow_dirty=True)
        (output / "unexpected.txt").write_text("fixture mutation", encoding="utf-8")
        export_handoff(root, "project/handoff-build", output, force=True, allow_dirty=True)
        self.assertFalse((output / "unexpected.txt").exists())

        unsafe = root / "data" / "handoffs" / "not-a-bundle"
        unsafe.mkdir(parents=True)
        (unsafe / "important.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(HandoffExportError, "previously generated handoff bundle"):
            export_handoff(root, "project/handoff-build", unsafe, force=True, allow_dirty=True)
        self.assertEqual("keep", (unsafe / "important.txt").read_text(encoding="utf-8"))

    def test_production_graph_impact_and_bundle_are_connected(self) -> None:
        root, _ = self.make_project()
        build_handoff(root, "project/handoff-build", generated_at=self.generated_at, research_commit=self.commit)
        graph = build_graph(root)
        node_ids = {node["id"] for node in graph["nodes"]}
        self.assertTrue({"PH001", "PP001", "PT001", "PT002", "HO001"}.issubset(node_ids))
        project_id = "project/handoff-build"
        edges = {(edge["from"], edge["to"], edge["type"]) for edge in graph["edges"]}
        self.assertIn((f"{project_id}::PH001", f"{project_id}::PP001", "prototyped_by"), edges)
        self.assertIn((f"{project_id}::PP001", f"{project_id}::HO001", "included_prototype"), edges)
        self.assertIn((f"{project_id}::AT001", f"{project_id}::HO001", "included_test"), edges)

        report = impact_report(graph, "HO001")
        self.assertTrue(report["found"])
        self.assertTrue({"EV001", "CL001", "IN001", "DC001", "PH001", "PP001", "RQ001", "AT001"}.issubset({item["id"] for item in report["upstream"]}))
        bundle = build_bundle(root, "project/handoff-build", "production-agent")
        self.assertIn("04_decisions/production-hypotheses.yaml", bundle)
        self.assertIn("05_production/production-handoff.yaml", bundle)
        self.assertNotIn("evidence-ledger.jsonl", bundle)

    def test_export_rejects_quoted_local_paths_in_public_artifacts(self) -> None:
        root, project = self.make_project()
        build_handoff(root, "project/handoff-build", generated_at=self.generated_at, research_commit=self.commit)
        creative_direction = project / "05_production" / "creative-direction.md"
        creative_direction.write_text('Local reference: "/Users/masa/private-note"\n', encoding="utf-8")

        with self.assertRaisesRegex(HandoffExportError, "absolute or local path"):
            export_handoff(root, "project/handoff-build", root / "data" / "handoffs" / "unsafe", allow_dirty=True)

    def test_target_resolution_rejects_escape(self) -> None:
        root, _ = self.make_project()
        with self.assertRaisesRegex(HandoffInputError, "project/<lower-case"):
            resolve_project(root, "../outside")

    def test_target_validation_does_not_block_on_another_project(self) -> None:
        root, _ = self.make_project()
        other = create_project(root, "other-project", "Other Project", "creator/test", self.generated_at)
        other_manifest_path = other / "manifest.yaml"
        other_manifest = yaml.safe_load(other_manifest_path.read_text(encoding="utf-8"))
        other_manifest["project"]["status"] = "NOT_A_STATUS"
        other_manifest_path.write_text(yaml.safe_dump(other_manifest, sort_keys=False), encoding="utf-8")

        path = build_handoff(root, "project/handoff-build", generated_at=self.generated_at, research_commit=self.commit)
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
