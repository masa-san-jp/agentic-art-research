from __future__ import annotations

import hashlib
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

from build_handoff import build_handoff
from canonical import payload_sha256
from import_production_result import ResultImportError, import_production_result, result_sha256
from new_project import create_project


class FeedbackImportContractTest(unittest.TestCase):
    commit = "0123456789abcdef0123456789abcdef01234567"
    generated_at = "2026-08-11T15:00:00+09:00"
    result_at = "2026-08-11T16:00:00+09:00"

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
        project = create_project(root, "feedback-import", "Feedback Import", "creator/test", self.generated_at)
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
            evidence_path.read_text(encoding="utf-8").replace("project/harmony-study", "project/feedback-import"),
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
        build_handoff(root, "project/feedback-import", generated_at=self.generated_at, research_commit=self.commit)
        return root, project

    def configure_result_schema(self, root: Path) -> None:
        schema_path = root / "schemas" / "external" / "test-production-result.schema.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.invalid/test/production-result.schema.json",
            "type": "object",
            "required": [
                "schema_version",
                "result_id",
                "production_project_id",
                "production_commit",
                "generated_at",
                "accepted_handoff",
                "selection",
                "outputs",
                "test_results",
                "observations",
                "deviations",
                "incidents",
                "research_change_requests",
                "open_gaps",
                "integrity",
            ],
            "properties": {
                "schema_version": {"const": "1.0.0"},
                "result_id": {"type": "string", "pattern": "^PR[0-9]{3,}$"},
                "production_project_id": {"type": "string", "minLength": 1},
                "production_commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                "generated_at": {"type": "string", "minLength": 1},
                "accepted_handoff": {
                    "type": "object",
                    "required": ["id", "content_sha256", "research_project_id", "research_commit"],
                    "properties": {
                        "id": {"type": "string"},
                        "content_sha256": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                        "research_project_id": {"type": "string"},
                        "research_commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                    },
                },
                "selection": {"type": "object"},
                "outputs": {"type": "array", "items": {"type": "object"}},
                "test_results": {"type": "array", "items": {"type": "object"}},
                "observations": {"type": "array", "items": {"type": "object"}},
                "deviations": {"type": "array", "items": {"type": "object"}},
                "incidents": {"type": "array", "items": {"type": "object"}},
                "research_change_requests": {"type": "array", "items": {"type": "object"}},
                "open_gaps": {"type": "array", "items": {"type": "object"}},
                "integrity": {
                    "type": "object",
                    "required": ["content_sha256"],
                    "properties": {"content_sha256": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}},
                },
            },
        }
        schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        policy_path = root / "config" / "handoff-policy.yaml"
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        policy["production_result_schema_path"] = "schemas/external/test-production-result.schema.json"
        policy["production_result_schema_versions"] = ["1.0.0"]
        policy["production_result_schema_source"] = {
            "repository": "masa-san-jp/agentic-art-production",
            "commit": self.commit,
            "acquired_at": self.result_at,
            "sha256": f"sha256:{hashlib.sha256(schema_path.read_bytes()).hexdigest()}",
        }
        policy_path.write_text(yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def make_result(self, project: Path, *, result_id: str = "PR001") -> dict[str, object]:
        handoff = yaml.safe_load((project / "05_production" / "production-handoff.yaml").read_text(encoding="utf-8"))
        result: dict[str, object] = {
            "schema_version": "1.0.0",
            "result_id": result_id,
            "production_project_id": "production/feedback-import-v1",
            "production_commit": self.commit,
            "generated_at": self.result_at,
            "accepted_handoff": {
                "id": handoff["handoff_id"],
                "content_sha256": handoff["integrity"]["content_sha256"],
                "research_project_id": "project/feedback-import",
                "research_commit": self.commit,
            },
            "selection": {"selected_hypothesis_id": "PH001", "authority": "HUMAN", "approval_ref": "AP001"},
            "outputs": [],
            "test_results": [
                {
                    "acceptance_test_id": "AT001",
                    "result": "PASS",
                    "executed_at": self.result_at,
                    "conditions": "fixed-lighting-fixture",
                    "evidence_ref": "urn:production:test:AT001",
                }
            ],
            "observations": [
                {
                    "id": "OB001",
                    "statement": "The interruption was observable from the entrance.",
                    "method": "authorized-observer-record",
                    "limitations": "Synthetic fixture with one observer.",
                    "related_requirement_ids": ["RQ001"],
                }
            ],
            "deviations": [],
            "incidents": [],
            "research_change_requests": [],
            "open_gaps": [],
            "integrity": {},
        }
        result["integrity"] = {"content_sha256": result_sha256(result)}
        return result

    def write_result(self, root: Path, result: dict[str, object], name: str = "result.json") -> Path:
        path = root / name
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_import_is_fail_closed_without_production_schema(self) -> None:
        root, project = self.make_project()
        result_path = self.write_result(root, self.make_result(project))

        with self.assertRaisesRegex(ResultImportError, "EXTERNAL-SCHEMA"):
            import_production_result(root, result_path, dry_run=True)

    def test_dry_run_is_read_only_and_apply_is_idempotent(self) -> None:
        root, project = self.make_project()
        self.configure_result_schema(root)
        result_path = self.write_result(root, self.make_result(project))
        tracked = [
            project / "02_evidence" / "evidence-ledger.jsonl",
            project / "06_governance" / "production-change-requests.yaml",
            project / "07_runtime" / "production-feedback-imports.jsonl",
            project / "07_runtime" / "run-log.jsonl",
            project / "manifest.yaml",
            project / "07_runtime" / "research-state.json",
        ]
        before = {path: path.read_bytes() for path in tracked}

        preview = import_production_result(root, result_path, dry_run=True)
        self.assertEqual("DRY_RUN", preview["status"])
        self.assertEqual("NONE", preview["impact_level"])
        self.assertEqual(before, {path: path.read_bytes() for path in tracked})

        applied = import_production_result(root, result_path, apply=True)
        self.assertEqual("APPLIED", applied["status"])
        self.assertEqual(["EV003", "EV004"], applied["evidence_candidate_ids"])
        self.assertEqual(
            2,
            (project / "02_evidence" / "evidence-ledger.jsonl")
            .read_text(encoding="utf-8")
            .count("urn:agentic-art-production:result:PR001"),
        )
        repeated_before = {path: path.read_bytes() for path in tracked}
        repeated = import_production_result(root, result_path, apply=True)
        self.assertEqual("ALREADY_APPLIED", repeated["status"])
        self.assertEqual(repeated_before, {path: path.read_bytes() for path in tracked})

    def test_same_result_id_with_different_content_is_rejected(self) -> None:
        root, project = self.make_project()
        self.configure_result_schema(root)
        original_path = self.write_result(root, self.make_result(project))
        import_production_result(root, original_path, apply=True)
        changed = self.make_result(project)
        changed["observations"][0]["statement"] = "A materially different observation."
        changed["integrity"] = {"content_sha256": result_sha256(changed)}
        changed_path = self.write_result(root, changed, "changed.json")

        with self.assertRaisesRegex(ResultImportError, "FEEDBACK-IDEMPOTENCY"):
            import_production_result(root, changed_path, apply=True)

    def test_partial_effect_retries_without_duplicate_records(self) -> None:
        root, project = self.make_project()
        self.configure_result_schema(root)
        result_path = self.write_result(root, self.make_result(project))
        import_production_result(root, result_path, apply=True)

        feedback_path = project / "07_runtime/production-feedback-imports.jsonl"
        run_log_path = project / "07_runtime/run-log.jsonl"
        run_log_path.write_text("", encoding="utf-8")
        recovered = import_production_result(root, result_path, apply=True)
        self.assertEqual("APPLIED", recovered["status"])
        self.assertEqual(1, len([line for line in feedback_path.read_text(encoding="utf-8").splitlines() if line]))
        run_events = [json.loads(line) for line in (project / "07_runtime/run-log.jsonl").read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(1, sum(event.get("event_type") == "PRODUCTION_FEEDBACK_IMPORTED" for event in run_events))
        evidence = [line for line in (project / "02_evidence/evidence-ledger.jsonl").read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(4, len(evidence))

    def test_major_reopens_and_critical_waits_for_human_approval(self) -> None:
        root, project = self.make_project()
        self.configure_result_schema(root)
        major = self.make_result(project, result_id="PR002")
        major["test_results"][0]["result"] = "FAIL"
        major["integrity"] = {"content_sha256": result_sha256(major)}
        major_path = self.write_result(root, major, "major.json")
        major_summary = import_production_result(root, major_path, apply=True)
        self.assertEqual("MAJOR", major_summary["impact_level"])
        self.assertTrue(major_summary["research_reopened"])
        manifest = yaml.safe_load((project / "manifest.yaml").read_text(encoding="utf-8"))
        self.assertEqual("ANALYZING", manifest["project"]["status"])

        critical = self.make_result(project, result_id="PR003")
        critical["incidents"] = [{"id": "INC001", "severity": "CRITICAL", "statement": "Synthetic safety incident."}]
        critical["integrity"] = {"content_sha256": result_sha256(critical)}
        critical_path = self.write_result(root, critical, "critical.json")
        critical_summary = import_production_result(root, critical_path, apply=True)
        self.assertEqual("CRITICAL", critical_summary["impact_level"])
        self.assertTrue(critical_summary["human_approval_required"])
        self.assertFalse(critical_summary["research_reopened"])
        change_requests = yaml.safe_load((project / "06_governance" / "production-change-requests.yaml").read_text(encoding="utf-8"))
        self.assertEqual("PENDING_HUMAN_APPROVAL", change_requests["change_requests"][0]["status"])

    def test_result_security_boundary_rejects_private_marker(self) -> None:
        root, project = self.make_project()
        self.configure_result_schema(root)
        result = self.make_result(project)
        result["observations"][0]["statement"] = "PRIVATE_RAW must never be imported."
        result["integrity"] = {"content_sha256": result_sha256(result)}
        result_path = self.write_result(root, result)

        with self.assertRaisesRegex(ResultImportError, "FEEDBACK-SECURITY"):
            import_production_result(root, result_path, dry_run=True)


if __name__ == "__main__":
    unittest.main()
