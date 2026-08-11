from __future__ import annotations

import copy
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

from canonical import handoff_sha256
from new_project import create_project
from validate import validate_repository


class HandoffValidationTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def make_handoff_project(self) -> Path:
        root = self.make_root()
        project = create_project(root, "handoff-project", "Handoff Project", "creator/test", "2026-08-11T15:00:00+09:00")
        fixture = FIXTURE_ROOT / "harmony"
        for source in sorted(fixture.rglob("*")):
            relative = source.relative_to(fixture)
            if relative == Path("metadata.yaml") or relative == Path("manifest.yaml") or source.is_dir():
                continue
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        evidence_path = project / "02_evidence" / "evidence-ledger.jsonl"
        evidence_path.write_text(
            evidence_path.read_text(encoding="utf-8").replace("project/harmony-study", "project/handoff-project"),
            encoding="utf-8",
        )

        manifest_path = project / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["workflow_mode"] = "PRODUCTION_HANDOFF"
        manifest["project"]["status"] = "COMPLETE_WITH_GAPS"
        manifest["project"]["version"] = "0.1.0"
        manifest["project"]["updated_at"] = "2026-08-11T15:00:00+09:00"
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
        hypothesis["single_hypothesis_rationale"] = "Only one candidate survives the adopted perceptual decision without weakening the intended experience."
        plan = json.loads((FIXTURE_ROOT / "schema-valid" / "prototype-plan.json").read_text(encoding="utf-8"))
        comparison = {"comparisons": []}
        handoff = json.loads((FIXTURE_ROOT / "schema-valid" / "production-handoff.json").read_text(encoding="utf-8"))
        handoff["research_project_id"] = "project/handoff-project"
        handoff["research_project_version"] = "0.1.0"
        handoff["selection"]["alternative_hypothesis_ids"] = []
        handoff["requirements"][0]["statement"] = yaml.safe_load(
            (project / "05_production" / "production-requirements.yaml").read_text(encoding="utf-8")
        )["requirements"][0]["statement"]
        handoff["integrity"]["content_sha256"] = handoff_sha256(handoff)

        (project / "04_decisions" / "production-hypotheses.yaml").write_text(
            yaml.safe_dump({"hypotheses": [hypothesis]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        (project / "04_decisions" / "hypothesis-comparison.yaml").write_text(
            yaml.safe_dump(comparison, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        (project / "05_production" / "prototype-plans.yaml").write_text(
            yaml.safe_dump({"prototype_plans": [plan]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        (project / "05_production" / "production-handoff.yaml").write_text(
            yaml.safe_dump(handoff, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return project

    def findings_for(self, project: Path) -> list:
        return validate_repository(project.parents[1])

    def test_valid_handoff_passes_and_canonical_hash_is_sensitive(self) -> None:
        project = self.make_handoff_project()
        self.assertEqual([], self.findings_for(project))

        handoff = yaml.safe_load((project / "05_production" / "production-handoff.yaml").read_text(encoding="utf-8"))
        original_hash = handoff_sha256(handoff)
        handoff["constraints"]["safety"][0] += "."
        self.assertNotEqual(original_hash, handoff_sha256(handoff))

    def test_handoff_blocking_mutations_have_named_rules(self) -> None:
        mutations = {
            "hash": ("HANDOFF-HASH", lambda handoff, project: handoff["integrity"].update({"content_sha256": "sha256:" + "0" * 64})),
            "private": ("HANDOFF-SECURITY", lambda handoff, project: handoff["constraints"]["privacy"].append("PRIVATE_RAW source")),
            "absolute-path": ("HANDOFF-SECURITY", lambda handoff, project: handoff["constraints"]["safety"].append("/private/local/file")),
            "signed-url": ("HANDOFF-SECURITY", lambda handoff, project: handoff["constraints"]["privacy"].append("https://example.invalid/a?X-Amz-Signature=secret")),
            "missing-requirement": ("HANDOFF-REQUIREMENT", lambda handoff, project: handoff.update({"requirements": []})),
            "handoff-reference": ("HANDOFF-REFERENCE", lambda handoff, project: handoff.update({"research_project_id": "project/other"})),
            "selection": ("HANDOFF-SELECTION", lambda handoff, project: handoff["selection"].update({"status": "HUMAN_SELECTION_REQUIRED", "selected_hypothesis_id": "PH001", "human_approval_required": True})),
            "supersede-cycle": ("HANDOFF-LIFECYCLE", lambda handoff, project: handoff.update({"supersedes": "HO001", "revision": 2})),
            "blocking-gap": ("HANDOFF-READINESS", lambda handoff, project: handoff["open_gaps"][0].update({"blocking": True})),
        }
        for name, (rule, mutate) in mutations.items():
            with self.subTest(mutation=name):
                project = self.make_handoff_project()
                path = project / "05_production" / "production-handoff.yaml"
                handoff = yaml.safe_load(path.read_text(encoding="utf-8"))
                mutate(handoff, project)
                path.write_text(yaml.safe_dump(handoff, sort_keys=False, allow_unicode=True), encoding="utf-8")
                findings = self.findings_for(project)
                self.assertTrue(any(item.rule == rule for item in findings), [item.render() for item in findings])

        project = self.make_handoff_project()
        policy_path = project.parents[1] / "config" / "handoff-policy.yaml"
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        policy["max_payload_bytes"] = 10
        policy_path.write_text(yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "HANDOFF-SECURITY" for item in findings))

        project = self.make_handoff_project()
        (project / "05_production" / "production-handoff.yaml").unlink()
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "HANDOFF-STRUCTURE" for item in findings))

        project = self.make_handoff_project()
        decision_path = project / "04_decisions" / "decision-log.yaml"
        decision_doc = yaml.safe_load(decision_path.read_text(encoding="utf-8"))
        decision_doc["decisions"][0]["status"] = "PROPOSED"
        decision_path.write_text(yaml.safe_dump(decision_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "HYPOTHESIS-TRACE" for item in findings))

        project = self.make_handoff_project()
        hypothesis_path = project / "04_decisions" / "production-hypotheses.yaml"
        hypothesis_doc = yaml.safe_load(hypothesis_path.read_text(encoding="utf-8"))
        hypothesis_doc["hypotheses"][0]["uncertainties"][0]["prototype_plan_ids"] = []
        hypothesis_path.write_text(yaml.safe_dump(hypothesis_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "UNCERTAINTY-PROTOTYPE" for item in findings))

        project = self.make_handoff_project()
        plan_path = project / "05_production" / "prototype-plans.yaml"
        plan_doc = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan_doc["prototype_plans"][0]["status"] = "EXTERNAL_VALIDATION_REQUIRED"
        plan_doc["prototype_plans"][0]["external_validation_reason"] = None
        plan_path.write_text(yaml.safe_dump(plan_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "PROTOTYPE-EXTERNAL" for item in findings))

        project = self.make_handoff_project()
        feedback_path = project / "07_runtime" / "production-feedback-imports.jsonl"
        feedback_path.write_text(json.dumps({"schema_version": "1.0.0", "result_id": "PR001"}) + "\n", encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "EXTERNAL-SCHEMA" for item in findings))

    def test_singleton_requires_rationale_and_prototype_dag_is_acyclic(self) -> None:
        project = self.make_handoff_project()
        hypothesis_path = project / "04_decisions" / "production-hypotheses.yaml"
        hypothesis_doc = yaml.safe_load(hypothesis_path.read_text(encoding="utf-8"))
        hypothesis_doc["hypotheses"][0]["single_hypothesis_rationale"] = None
        hypothesis_path.write_text(yaml.safe_dump(hypothesis_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "HYPOTHESIS-SINGLETON" for item in findings))

        project = self.make_handoff_project()
        plan_path = project / "05_production" / "prototype-plans.yaml"
        plan_doc = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan_doc["prototype_plans"][0]["tasks"][0]["depends_on"] = ["PT002"]
        plan_path.write_text(yaml.safe_dump(plan_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "PROTOTYPE-DAG" for item in findings))

        project = self.make_handoff_project()
        plan_path = project / "05_production" / "prototype-plans.yaml"
        plan_doc = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan_doc["prototype_plans"][0]["tasks"][1]["depends_on"] = ["PT999"]
        plan_path.write_text(yaml.safe_dump(plan_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "PROTOTYPE-DAG" for item in findings))

        project = self.make_handoff_project()
        plan_path = project / "05_production" / "prototype-plans.yaml"
        plan_doc = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plan_doc["prototype_plans"][0]["tasks"].append(copy.deepcopy(plan_doc["prototype_plans"][0]["tasks"][0]))
        plan_path.write_text(yaml.safe_dump(plan_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "PROTOTYPE-DAG" for item in findings))

    def test_multiple_hypotheses_require_a_common_comparison(self) -> None:
        project = self.make_handoff_project()
        hypotheses_path = project / "04_decisions" / "production-hypotheses.yaml"
        hypothesis_doc = yaml.safe_load(hypotheses_path.read_text(encoding="utf-8"))
        second = copy.deepcopy(hypothesis_doc["hypotheses"][0])
        second["id"] = "PH002"
        second["title"] = "Alternative interrupted interval installation"
        second["proposition"] = "A fixed light cue announces the interruption instead of viewer movement."
        second["recommendation"] = "ALTERNATIVE"
        second["single_hypothesis_rationale"] = None
        second["uncertainties"] = []
        hypothesis_doc["hypotheses"].append(second)
        hypotheses_path.write_text(yaml.safe_dump(hypothesis_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "HYPOTHESIS-COMPARISON" for item in findings))

        comparison_path = project / "04_decisions" / "hypothesis-comparison.yaml"
        comparison = json.loads((FIXTURE_ROOT / "schema-valid" / "hypothesis-comparison.json").read_text(encoding="utf-8"))
        comparison_path.write_text(yaml.safe_dump({"comparisons": [comparison]}, sort_keys=False, allow_unicode=True), encoding="utf-8")
        handoff_path = project / "05_production" / "production-handoff.yaml"
        handoff = yaml.safe_load(handoff_path.read_text(encoding="utf-8"))
        handoff["selection"]["alternative_hypothesis_ids"] = ["PH002"]
        handoff["integrity"]["content_sha256"] = handoff_sha256(handoff)
        handoff_path.write_text(yaml.safe_dump(handoff, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.assertEqual([], self.findings_for(project))

    def test_feedback_requires_production_owned_schema_snapshot(self) -> None:
        project = self.make_handoff_project()
        feedback_path = project / "07_runtime" / "production-feedback-imports.jsonl"
        feedback_path.write_text(json.dumps({"schema_version": "1.0.0", "result_id": "PR001"}) + "\n", encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "EXTERNAL-SCHEMA" for item in findings), [item.render() for item in findings])

    def test_uncertainty_and_requirement_snapshots_remain_mutually_traceable(self) -> None:
        project = self.make_handoff_project()
        hypothesis_path = project / "04_decisions" / "production-hypotheses.yaml"
        hypotheses = yaml.safe_load(hypothesis_path.read_text(encoding="utf-8"))
        hypotheses["hypotheses"][0]["uncertainties"][0]["prototype_plan_ids"] = ["PP999"]
        hypothesis_path.write_text(yaml.safe_dump(hypotheses, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "UNCERTAINTY-PROTOTYPE" for item in findings), [item.render() for item in findings])

        project = self.make_handoff_project()
        plan_path = project / "05_production" / "prototype-plans.yaml"
        plans = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
        plans["prototype_plans"][0]["uncertainty_ids"] = ["U999"]
        plan_path.write_text(yaml.safe_dump(plans, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "UNCERTAINTY-PROTOTYPE" for item in findings), [item.render() for item in findings])

        project = self.make_handoff_project()
        handoff_path = project / "05_production" / "production-handoff.yaml"
        handoff = yaml.safe_load(handoff_path.read_text(encoding="utf-8"))
        handoff["requirements"][0]["source_decision_ids"] = ["DC999"]
        handoff["requirements"][0]["acceptance_test_ids"] = ["AT999"]
        handoff["integrity"]["content_sha256"] = handoff_sha256(handoff)
        handoff_path.write_text(yaml.safe_dump(handoff, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "HANDOFF-REQUIREMENT" for item in findings), [item.render() for item in findings])

    def test_selection_supersedes_security_and_schema_versions_fail_closed(self) -> None:
        project = self.make_handoff_project()
        handoff_path = project / "05_production" / "production-handoff.yaml"
        handoff = yaml.safe_load(handoff_path.read_text(encoding="utf-8"))
        handoff["selection"]["selected_hypothesis_id"] = None
        handoff["integrity"]["content_sha256"] = handoff_sha256(handoff)
        handoff_path.write_text(yaml.safe_dump(handoff, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "HANDOFF-SELECTION" for item in findings), [item.render() for item in findings])

        project = self.make_handoff_project()
        handoff_path = project / "05_production" / "production-handoff.yaml"
        handoff = yaml.safe_load(handoff_path.read_text(encoding="utf-8"))
        handoff.update({"handoff_id": "HO002", "revision": 2, "supersedes": "HO001"})
        handoff["constraints"]["safety"].append("Distribution is unrestricted; compare input / output.")
        handoff["integrity"]["content_sha256"] = handoff_sha256(handoff)
        handoff_path.write_text(yaml.safe_dump(handoff, sort_keys=False, allow_unicode=True), encoding="utf-8")
        findings = self.findings_for(project)
        self.assertFalse(any(item.rule in {"HANDOFF-LIFECYCLE", "HANDOFF-SECURITY"} for item in findings), [item.render() for item in findings])

        schema_path = project.parents[1] / "schemas" / "external" / "production-result.v1.schema.json"
        schema_path.write_text(
            json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}) + "\n",
            encoding="utf-8",
        )
        feedback_path = project / "07_runtime" / "production-feedback-imports.jsonl"
        feedback_path.write_text(json.dumps({"schema_version": "1.0.0", "result_id": "PR001"}) + "\n", encoding="utf-8")
        findings = self.findings_for(project)
        self.assertTrue(any(item.rule == "EXTERNAL-SCHEMA" for item in findings), [item.render() for item in findings])


if __name__ == "__main__":
    unittest.main()
