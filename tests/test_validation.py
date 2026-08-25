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
from validate import Finding, validate_repository


def _declare_finished(project, status: str) -> None:
    """Put a fixture project into a status that claims the work is over."""
    state_path = project / "07_runtime" / "research-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = status
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path = project / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["project"]["status"] = status
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report_path = project / "07_runtime" / "completion-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = status
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ValidationErrorContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_schema_error_reports_file_field_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "invalid-manifest", "Invalid Manifest")
        manifest_path = project / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["access"]["classification"] = "NOT_A_CLASS"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "SCHEMA:enum")
        self.assertEqual("projects/invalid-manifest/manifest.yaml", finding.path)
        self.assertEqual("access.classification", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_jsonl_parse_error_reports_file_line_field_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "invalid-jsonl", "Invalid JSONL")
        claims_path = project / "03_knowledge" / "claims.jsonl"
        claims_path.write_text(json.dumps({"id": "CL001"}) + "\nnot-json\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "JSONL")
        self.assertEqual("projects/invalid-jsonl/03_knowledge/claims.jsonl", finding.path)
        self.assertEqual(2, finding.line)
        self.assertEqual("$", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_duplicate_yaml_key_reports_file_line_field_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "duplicate-yaml", "Duplicate YAML")
        question_path = project / "01_planning" / "question-register.yaml"
        question_path.write_text("questions: []\nquestions: []\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "YAML")
        self.assertEqual("projects/duplicate-yaml/01_planning/question-register.yaml", finding.path)
        self.assertEqual(2, finding.line)
        self.assertEqual("questions", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_forbidden_filename_fails_repository_boundary(self) -> None:
        root = self.make_root()
        project = create_project(root, "forbidden-name", "Forbidden Name")
        env_path = project / ".env"
        env_path.write_text("TOKEN=synthetic", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.path.endswith("/.env"))
        self.assertEqual("DATA-BOUNDARY", finding.rule)
        self.assertIn("remediation:", finding.render())

    def test_likely_secret_reports_file_line_rule_and_remediation(self) -> None:
        root = self.make_root()
        project = create_project(root, "secret-scan", "Secret Scan")
        source_path = project / "02_evidence" / "source.txt"
        source_path.write_text("api_" + "key=" + "a" * 32 + "\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "SECRET-SCAN")
        self.assertEqual("projects/secret-scan/02_evidence/source.txt", finding.path)
        self.assertEqual(1, finding.line)
        self.assertEqual("$", finding.field)
        self.assertIn("remediation:", finding.render())

    def test_private_record_in_approved_snapshot_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "private-snapshot", "Private Snapshot")
        snapshot_path = project / "02_evidence" / "approved-snapshots" / "records.jsonl"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps({"sensitivity": "PRIVATE_RAW"}) + "\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "DATA-BOUNDARY" and item.field == "sensitivity")
        self.assertEqual(1, finding.line)
        self.assertIn("remediation:", finding.render())

    def test_broken_cross_reference_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "broken-reference", "Broken Reference")
        evidence = {
            "id": "EV001",
            "source_type": "primary_public",
            "source_location": "https://example.invalid/source/001",
            "created_at": "unknown",
            "acquired_at": "2026-08-11T15:00:00+09:00",
            "content_hash": "sha256:" + "0" * 64,
            "rights_status": "public-use",
            "sensitivity": "PUBLIC_CITABLE",
            "redistribution": "allowed",
            "related_projects": ["project/broken-reference"],
            "related_questions": ["Q999"],
            "extraction_status": "processed",
            "direct_observation": False,
            "observed_by": "collector",
        }
        (project / "02_evidence" / "evidence-ledger.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "CROSS-REFERENCE")
        self.assertEqual("evidence-ledger.jsonl", Path(finding.path).name)
        self.assertEqual("EV001.related_questions[0]", finding.field)

    def test_mandatory_question_left_open_by_a_finished_project_is_reported(self) -> None:
        """A question is asked before it is answered, so "still open" is only wrong once the project says it is done."""
        root = self.make_root()
        project = create_project(root, "open-question", "Open Question")
        question_path = project / "01_planning" / "question-register.yaml"
        question_path.write_text(
            yaml.safe_dump(
                {"questions": [{"id": "Q001", "text": "Question", "priority": "mandatory", "status": "OPEN"}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        _declare_finished(project, "COMPLETE_WITH_GAPS")

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "QUESTION-TERMINAL")
        self.assertEqual("Q001.status", finding.field)

    def test_a_project_still_working_may_hold_an_open_mandatory_question(self) -> None:
        root = self.make_root()
        project = create_project(root, "working-question", "Working Question")
        (project / "01_planning" / "question-register.yaml").write_text(
            yaml.safe_dump(
                {"questions": [{"id": "Q001", "text": "Question", "priority": "mandatory", "status": "OPEN"}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        findings = validate_repository(root)

        self.assertEqual([], [item for item in findings if item.rule == "QUESTION-TERMINAL"])

    def test_illegal_lifecycle_transition_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "illegal-transition", "Illegal Transition")
        run_log = project / "07_runtime" / "run-log.jsonl"
        run_log.write_text(
            json.dumps({"event_id": "EVT001", "event_type": "STATE_TRANSITION", "from_status": "DRAFT", "to_status": "COMPLETE"}) + "\n",
            encoding="utf-8",
        )

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "LIFECYCLE-TRANSITION")
        self.assertEqual(1, finding.line)
        self.assertEqual("from_status/to_status", finding.field)

    def test_mandatory_requirement_needs_resolved_acceptance_test(self) -> None:
        root = self.make_root()
        project = create_project(root, "untestable-requirement", "Untestable Requirement")
        requirements_path = project / "05_production" / "production-requirements.yaml"
        requirements_path.write_text(
            yaml.safe_dump(
                {
                    "requirements": [
                        {
                            "id": "RQ001",
                            "category": "visual",
                            "statement": "A testable visual constraint.",
                            "source_decisions": [],
                            "priority": "mandatory",
                            "acceptance_test_ids": [],
                            "status": "ADOPTED",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "REQUIREMENT-TEST")
        self.assertEqual("RQ001.acceptance_test_ids", finding.field)

    def test_duplicate_canonical_id_fails(self) -> None:
        root = self.make_root()
        project = create_project(root, "duplicate-id", "Duplicate ID")
        base = {
            "id": "EV001",
            "source_type": "primary_public",
            "source_location": "https://example.invalid/source/001",
            "created_at": "unknown",
            "acquired_at": "2026-08-11T15:00:00+09:00",
            "content_hash": "sha256:" + "0" * 64,
            "rights_status": "public-use",
            "sensitivity": "PUBLIC_CITABLE",
            "redistribution": "allowed",
            "related_projects": ["project/duplicate-id"],
            "related_questions": [],
            "extraction_status": "processed",
            "direct_observation": False,
            "observed_by": "collector",
        }
        (project / "02_evidence" / "evidence-ledger.jsonl").write_text(
            json.dumps(base) + "\n" + json.dumps(base) + "\n", encoding="utf-8"
        )

        findings = validate_repository(root)
        finding = next(item for item in findings if item.rule == "DUPLICATE-ID")
        self.assertEqual("id", finding.field)
        self.assertIn("EV001", finding.message)

    def test_typed_decision_registries_resolve_in_both_directions(self) -> None:
        root = self.make_root()
        project = create_project(root, "typed-decision", "Typed Decision")
        evidence = {
            "id": "EV001",
            "source_type": "primary_public",
            "source_location": "https://example.invalid/source/001",
            "creator": "creator/fixture",
            "created_at": "unknown",
            "acquired_at": "2026-08-11T15:00:00+09:00",
            "content_hash": "sha256:" + "0" * 64,
            "rights_status": "public-use",
            "sensitivity": "PUBLIC_CITABLE",
            "redistribution": "allowed",
            "related_projects": ["project/typed-decision"],
            "related_questions": [],
            "extraction_status": "processed",
            "direct_observation": False,
            "observed_by": "collector",
        }
        (project / "02_evidence" / "evidence-ledger.jsonl").write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        (project / "04_decisions" / "decision-log.yaml").write_text(
            yaml.safe_dump(
                {
                    "decisions": [
                        {
                            "id": "DC001",
                            "question": "Which option should be adopted?",
                            "selected_option": "Use the restrained option.",
                            "rejected_options": [],
                            "rejected_option_ids": ["RO001"],
                            "insight_ids": [],
                            "evidence_ids": ["EV001"],
                            "reason": "The evidence supports the restrained option.",
                            "uncertainty": "The audience response remains untested.",
                            "uncertainty_ids": ["U001"],
                            "review_trigger": "Prototype review fails.",
                            "authority": "agent-recommended",
                            "status": "ADOPTED",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "04_decisions" / "rejected-options.yaml").write_text(
            yaml.safe_dump(
                {"rejected_options": [{"id": "RO001", "title": "Use the explicit option", "reason": "It weakens the intended absence.", "decision_ids": ["DC001"]}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "04_decisions" / "uncertainty-register.yaml").write_text(
            yaml.safe_dump(
                {"uncertainties": [{"id": "U001", "statement": "The audience response remains untested.", "severity": "MAJOR", "decision_ids": ["DC001"], "review_trigger": "Prototype review fails."}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        self.assertEqual([], validate_repository(root))

    def test_typed_decision_registry_reverse_reference_is_blocking(self) -> None:
        root = self.make_root()
        project = create_project(root, "broken-decision-registry", "Broken Decision Registry")
        (project / "04_decisions" / "decision-log.yaml").write_text(
            yaml.safe_dump(
                {
                    "decisions": [
                        {
                            "id": "DC001",
                            "question": "Which option should be adopted?",
                            "selected_option": "Use the restrained option.",
                            "rejected_options": [],
                            "rejected_option_ids": [],
                            "insight_ids": [],
                            "evidence_ids": ["EV001"],
                            "reason": "The evidence supports the restrained option.",
                            "uncertainty": None,
                            "review_trigger": None,
                            "authority": "agent-recommended",
                            "status": "PROPOSED",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "04_decisions" / "rejected-options.yaml").write_text(
            "rejected_options:\n  - id: RO001\n    title: Explicit option\n    reason: It weakens the intended absence.\n    decision_ids: [DC001]\n",
            encoding="utf-8",
        )
        findings = validate_repository(root)
        self.assertTrue(any(item.rule == "DECISION-REGISTRY-REVERSE" for item in findings))


class ExecutionQueueStateValidationTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def write_execution(self, root: Path, tasks: list[dict], **state_overrides: object) -> None:
        execution = root / "execution"
        execution.mkdir()
        queue = {
            "version": 5,
            "selection_policy": "lowest_id_ready_with_dependencies_done",
            "allowed_statuses": ["BACKLOG", "READY", "IN_PROGRESS", "BLOCKED", "DONE"],
            "tasks": tasks,
        }
        state: dict[str, object] = {
            "version": 1,
            "next_task": "BOUNDARY-001",
            "last_completed_task": "RUNTIME-005",
            "terminal": False,
        }
        state.update(state_overrides)
        (execution / "task-queue.yaml").write_text(yaml.safe_dump(queue, sort_keys=False), encoding="utf-8")
        (execution / "state.yaml").write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")

    def base_tasks(self) -> list[dict]:
        return [
            {"id": f"RUNTIME-{number:03d}", "status": "DONE", "depends_on": []}
            for number in range(1, 6)
        ] + [{"id": "BOUNDARY-001", "status": "READY", "depends_on": ["RUNTIME-005"]}]

    def test_queue_and_state_are_valid_when_next_task_is_lowest_ready(self) -> None:
        root = self.make_root()
        self.write_execution(root, self.base_tasks())

        findings = validate_repository(root)

        self.assertEqual([], [finding for finding in findings if finding.rule.startswith("EXECUTION-")])

    def test_missing_runtime_task_is_a_named_blocking_rule(self) -> None:
        root = self.make_root()
        tasks = [task for task in self.base_tasks() if task["id"] != "RUNTIME-005"]
        self.write_execution(root, tasks)

        findings = validate_repository(root)

        self.assertTrue(any(finding.rule == "EXECUTION-RUNTIME-TASK-MISSING" for finding in findings))

    def test_unknown_dependency_is_rejected(self) -> None:
        root = self.make_root()
        tasks = self.base_tasks()
        tasks[-1]["depends_on"] = ["MISSING-001"]
        self.write_execution(root, tasks, next_task=None)

        findings = validate_repository(root)

        self.assertTrue(any(finding.rule == "EXECUTION-DEPENDENCY-MISSING" for finding in findings))

    def test_dependency_cycle_is_rejected(self) -> None:
        root = self.make_root()
        tasks = self.base_tasks()
        tasks[-1] = {"id": "BOUNDARY-001", "status": "BACKLOG", "depends_on": ["GAP-001"]}
        tasks.append({"id": "GAP-001", "status": "BACKLOG", "depends_on": ["BOUNDARY-001"]})
        self.write_execution(root, tasks, next_task=None)

        findings = validate_repository(root)

        self.assertTrue(any(finding.rule == "EXECUTION-DEPENDENCY-CYCLE" for finding in findings))

    def test_wrong_next_task_is_rejected(self) -> None:
        root = self.make_root()
        self.write_execution(root, self.base_tasks(), next_task="RUNTIME-005")

        findings = validate_repository(root)

        self.assertTrue(any(finding.rule == "EXECUTION-NEXT-TASK" for finding in findings))

    def test_terminal_true_is_rejected_until_all_tasks_are_done(self) -> None:
        root = self.make_root()
        self.write_execution(root, self.base_tasks(), terminal=True)

        findings = validate_repository(root)

        self.assertTrue(any(finding.rule == "EXECUTION-TERMINAL-EARLY" for finding in findings))


if __name__ == "__main__":
    unittest.main()
