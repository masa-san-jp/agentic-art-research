from __future__ import annotations

import argparse
import fnmatch
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from canonical import canonical_json_bytes, handoff_hash_payload, handoff_sha256
from completion_quality import CompletionQualityConfigError, load_completion_quality_policy
from _common import (
    InputParseError,
    PROJECT_REQUIRED_FILES,
    ROOT,
    iter_project_dirs,
    load_json,
    load_yaml,
    read_jsonl_with_lines,
)
from security_check import scan_advanced_security


SCHEMA_FOR_JSONL = {
    "02_evidence/evidence-ledger.jsonl": "evidence",
    "03_knowledge/claims.jsonl": "claim",
    "03_knowledge/observations.jsonl": "observation",
    "03_knowledge/prior-art.jsonl": "prior-art",
    "03_knowledge/relationships.jsonl": "relationship",
    "03_knowledge/contradictions.jsonl": "contradiction",
    "03_knowledge/external-references.jsonl": "external-reference",
}
SCHEMA_FOR_YAML_COLLECTION = {
    "04_decisions/insight-register.yaml": ("insights", "insight"),
    "04_decisions/decision-log.yaml": ("decisions", "decision"),
    "04_decisions/rejected-options.yaml": ("rejected_options", "rejected-option"),
    "04_decisions/uncertainty-register.yaml": ("uncertainties", "uncertainty"),
    "05_production/production-requirements.yaml": ("requirements", "requirement"),
    "04_decisions/self-repetition-review.yaml": ("reviews", "self-repetition-review"),
    "04_decisions/production-hypotheses.yaml": ("hypotheses", "production-hypothesis"),
    "04_decisions/hypothesis-comparison.yaml": ("comparisons", "hypothesis-comparison"),
    "05_production/prototype-plans.yaml": ("prototype_plans", "prototype-plan"),
}
SCHEMA_FOR_YAML_OBJECT = {
    "01_planning/research-plan.yaml": "research-plan",
    "05_production/visual-language.yaml": "visual-language",
    "05_production/production-handoff.yaml": "production-handoff",
    "00_intake/research-request.yaml": "research-request",
    "00_intake/research-request-receipt.yaml": "research-request-receipt",
}
SCHEMA_FOR_JSON = {
    "07_runtime/research-state.json": "research-state",
    "07_runtime/completion-report.json": "completion-report",
}
DOMAIN_SCHEMAS = (
    "project-manifest",
    "evidence",
    "claim",
    "observation",
    "relationship",
    "contradiction",
    "external-reference",
    "insight",
    "decision",
    "rejected-option",
    "uncertainty",
    "requirement",
    "production-hypothesis",
    "hypothesis-comparison",
    "prototype-plan",
    "visual-language",
    "production-handoff",
    "research-request",
    "research-request-receipt",
    "research-signal-export",
    "research-state",
    "completion-report",
    "research-plan",
    "prior-art",
    "self-repetition-review",
    "run-log-event",
)
RecordEntry = tuple[str, Path, int | None, dict[str, Any]]
REFERENCE_FIELDS = {
    "evidence": [("related_questions", "question"), ("related_projects", "project")],
    "claim": [("evidence_ids", "evidence"), ("supporting_claims", "claim"), ("opposing_claims", "claim")],
    "observation": [("evidence_ids", "evidence")],
    "relationship": [("evidence_ids", "evidence")],
    "contradiction": [("claim_ids", "claim")],
    "external-reference": [("evidence_ids", "evidence")],
    "insight": [("claim_ids", "claim"), ("opposing_claim_ids", "claim")],
    "decision": [
        ("insight_ids", "insight"),
        ("evidence_ids", "evidence"),
        ("rejected_option_ids", "rejected-option"),
        ("uncertainty_ids", "uncertainty"),
    ],
    "rejected-option": [("decision_ids", "decision")],
    "uncertainty": [("decision_ids", "decision")],
    "requirement": [("source_decisions", "decision"), ("acceptance_test_ids", "acceptance_test")],
    "acceptance_test": [("target_requirement", "requirement")],
    "production-hypothesis": [("source_decision_ids", "decision"), ("source_insight_ids", "insight")],
    "hypothesis-comparison": [("hypothesis_ids", "production-hypothesis"), ("recommended_hypothesis_id", "production-hypothesis")],
    "prototype-plan": [
        ("hypothesis_id", "production-hypothesis"),
        ("uncertainty_ids", "uncertainty"),
        ("acceptance_test_ids", "acceptance_test"),
    ],
    "self-repetition-review": [("prior_work_refs", "prior-art")],
    "prototype_task": [("depends_on", "prototype_task")],
    "production-handoff": [
        ("selected_hypothesis_id", "production-hypothesis"),
        ("alternative_hypothesis_ids", "production-hypothesis"),
        ("prototype_plan_ids", "prototype-plan"),
    ],
}


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    message: str
    line: int | None = None
    field: str | None = None
    remediation: str = "Inspect the reported value and correct the source file."

    def render(self) -> str:
        location = self.path
        if self.line is not None:
            location += f":{self.line}"
        if self.field:
            location += f"#{self.field}"
        return f"{location}: [{self.rule}] {self.message}; remediation: {self.remediation}"


def _check_execution_queue_state(root: Path, findings: list[Finding]) -> None:
    """Validate the repository-level execution queue/state SSOT.

    Project validation roots used by unit tests may intentionally omit the
    repository execution files. Once either file exists, however, both files
    are required and their scheduling contract is blocking.
    """
    queue_path = root / "execution" / "task-queue.yaml"
    state_path = root / "execution" / "state.yaml"
    if not queue_path.exists() and not state_path.exists():
        return
    if not queue_path.exists() or not state_path.exists():
        missing = queue_path if not queue_path.exists() else state_path
        findings.append(
            Finding(
                _relative_path(root, missing),
                "EXECUTION-SSOT",
                "execution/task-queue.yaml and execution/state.yaml must exist together",
                remediation="Restore both repository execution SSOT files, then rerun validation.",
            )
        )
        return

    try:
        queue = load_yaml(queue_path)
    except Exception as exc:
        findings.append(_exception_finding(root, queue_path, "EXECUTION-QUEUE-YAML", exc))
        return
    try:
        state = load_yaml(state_path)
    except Exception as exc:
        findings.append(_exception_finding(root, state_path, "EXECUTION-STATE-YAML", exc))
        return
    if not isinstance(queue, dict):
        findings.append(
            Finding(
                _relative_path(root, queue_path),
                "EXECUTION-QUEUE-SHAPE",
                "task queue must be a mapping",
                remediation="Make execution/task-queue.yaml a mapping with an allowed_statuses list and tasks list.",
            )
        )
        return
    if not isinstance(state, dict):
        findings.append(
            Finding(
                _relative_path(root, state_path),
                "EXECUTION-STATE-SHAPE",
                "execution state must be a mapping",
                remediation="Make execution/state.yaml a mapping with next_task, last_completed_task, and terminal.",
            )
        )
        return

    allowed_statuses = queue.get("allowed_statuses")
    allowed = set(allowed_statuses) if isinstance(allowed_statuses, list) and all(isinstance(item, str) for item in allowed_statuses) else set()
    tasks_value = queue.get("tasks")
    if not allowed:
        findings.append(
            Finding(
                _relative_path(root, queue_path),
                "EXECUTION-QUEUE-SHAPE",
                "allowed_statuses must be a non-empty list of strings",
                field="allowed_statuses",
                remediation="Declare the canonical queue statuses, including BACKLOG, READY, IN_PROGRESS, BLOCKED, and DONE.",
            )
        )
    if not isinstance(tasks_value, list):
        findings.append(
            Finding(
                _relative_path(root, queue_path),
                "EXECUTION-QUEUE-SHAPE",
                "tasks must be a list",
                field="tasks",
                remediation="Declare each execution task as one mapping in tasks.",
            )
        )
        return

    tasks: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(tasks_value):
        if not isinstance(raw, dict):
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-TASK-SHAPE",
                    f"tasks[{index}] must be a mapping",
                    field=f"tasks[{index}]",
                    remediation="Replace the task with a mapping containing id, status, and depends_on.",
                )
            )
            continue
        task_id = raw.get("id")
        if not isinstance(task_id, str) or not task_id:
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-TASK-ID",
                    f"tasks[{index}].id must be a non-empty string",
                    field=f"tasks[{index}].id",
                    remediation="Assign a unique stable execution task ID.",
                )
            )
            continue
        if task_id in tasks:
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-TASK-ID-DUPLICATE",
                    f"task ID {task_id!r} is duplicated",
                    field=f"tasks[{index}].id",
                    remediation="Keep exactly one queue entry for each task ID.",
                )
            )
            continue
        status = raw.get("status")
        if status not in allowed:
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-TASK-STATUS",
                    f"task {task_id} has invalid status {status!r}",
                    field=f"tasks[{index}].status",
                    remediation="Use a status from execution/task-queue.yaml#allowed_statuses.",
                )
            )
        dependencies = raw.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-DEPENDENCY-SHAPE",
                    f"task {task_id} depends_on must be a list of task IDs",
                    field=f"tasks[{index}].depends_on",
                    remediation="Use a list of existing task IDs without nested values.",
                )
            )
            dependencies = []
        if len(set(dependencies)) != len(dependencies):
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-DEPENDENCY-DUPLICATE",
                    f"task {task_id} depends_on contains duplicates",
                    field=f"tasks[{index}].depends_on",
                    remediation="List each dependency once.",
                )
            )
        tasks[task_id] = {"status": status, "depends_on": dependencies}

    for task_id, task in tasks.items():
        for dependency in task["depends_on"]:
            if dependency not in tasks:
                findings.append(
                    Finding(
                        _relative_path(root, queue_path),
                        "EXECUTION-DEPENDENCY-MISSING",
                        f"task {task_id} depends on unknown task {dependency}",
                        field=f"tasks[{task_id}].depends_on",
                        remediation="Add the dependency task or remove the stale dependency.",
                    )
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-DEPENDENCY-CYCLE",
                    f"task dependency cycle includes {task_id}",
                    remediation="Break the cycle so execution tasks form a DAG.",
                )
            )
            return
        if task_id in visited or task_id not in tasks:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(tasks):
        visit(task_id)

    in_progress = sorted(task_id for task_id, task in tasks.items() if task.get("status") == "IN_PROGRESS")
    if len(in_progress) > 1:
        findings.append(
            Finding(
                _relative_path(root, queue_path),
                "EXECUTION-IN-PROGRESS-MULTIPLE",
                f"more than one task is IN_PROGRESS: {', '.join(in_progress)}",
                remediation="Leave at most one execution task IN_PROGRESS before handing off the repository.",
            )
        )
    for task_id, task in tasks.items():
        if task.get("status") not in {"READY", "IN_PROGRESS"}:
            continue
        unresolved = [dependency for dependency in task["depends_on"] if dependency not in tasks or tasks[dependency].get("status") != "DONE"]
        if unresolved:
            findings.append(
                Finding(
                    _relative_path(root, queue_path),
                    "EXECUTION-DEPENDENCY-NOT-DONE",
                    f"task {task_id} is {task['status']} but dependencies are not DONE: {', '.join(unresolved)}",
                    field=f"tasks[{task_id}].depends_on",
                    remediation="Set the task BACKLOG/BLOCKED until every dependency is DONE.",
                )
            )
    ready = sorted(
        task_id
        for task_id, task in tasks.items()
        if task.get("status") == "READY"
        and all(dependency in tasks and tasks[dependency].get("status") == "DONE" for dependency in task["depends_on"])
    )

    required_runtime = {f"RUNTIME-{number:03d}" for number in range(1, 6)}
    for required_id in sorted(required_runtime - tasks.keys()):
        findings.append(
            Finding(
                _relative_path(root, queue_path),
                "EXECUTION-RUNTIME-TASK-MISSING",
                f"required runtime task {required_id} is missing",
                remediation="Restore the execution-plan runtime task before selecting a next action.",
            )
        )

    for field in ("next_task", "last_completed_task", "terminal"):
        if field not in state:
            findings.append(
                Finding(
                    _relative_path(root, state_path),
                    "EXECUTION-STATE-SHAPE",
                    f"state is missing {field}",
                    field=field,
                    remediation="Record the execution state field explicitly, using null where no task applies.",
                )
            )
    next_task = state.get("next_task")
    if next_task is not None and next_task not in tasks:
        findings.append(
            Finding(
                _relative_path(root, state_path),
                "EXECUTION-NEXT-TASK-UNKNOWN",
                f"next_task {next_task!r} is not present in the queue",
                field="next_task",
                remediation="Set next_task to a queue task ID or null when no task is ready.",
            )
        )
    expected_next = in_progress[0] if len(in_progress) == 1 else (ready[0] if ready else None)
    if next_task != expected_next:
        findings.append(
            Finding(
                _relative_path(root, state_path),
                "EXECUTION-NEXT-TASK",
                f"next_task is {next_task!r}, expected {expected_next!r}",
                field="next_task",
                remediation="Set next_task to the IN_PROGRESS task, or the lowest-ID READY task whose dependencies are DONE.",
            )
        )
    terminal = state.get("terminal")
    if terminal is True and any(task.get("status") != "DONE" for task in tasks.values()):
        findings.append(
            Finding(
                _relative_path(root, state_path),
                "EXECUTION-TERMINAL-EARLY",
                "terminal is true while one or more queue tasks are not DONE",
                field="terminal",
                remediation="Set terminal false until every queue task is DONE.",
            )
        )
    last_completed = state.get("last_completed_task")
    if last_completed is not None and (last_completed not in tasks or tasks[last_completed].get("status") != "DONE"):
        findings.append(
            Finding(
                _relative_path(root, state_path),
                "EXECUTION-LAST-COMPLETED",
                f"last_completed_task {last_completed!r} is not DONE in the queue",
                field="last_completed_task",
                remediation="Record the latest queue task whose status is DONE.",
            )
        )


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _exception_finding(root: Path, path: Path, rule: str, exc: Exception) -> Finding:
    if isinstance(exc, InputParseError):
        return Finding(
            _relative_path(root, exc.path),
            rule,
            exc.message,
            line=exc.line,
            field=exc.field,
            remediation="Fix the syntax or duplicate key at the reported location, then rerun validation.",
        )
    return Finding(
        _relative_path(root, path),
        rule,
        str(exc),
        remediation="Inspect the file and correct the reported input error, then rerun validation.",
    )


def _field_path(parts: list[Any] | tuple[Any, ...]) -> str:
    field = ""
    for part in parts:
        if isinstance(part, int):
            field += f"[{part}]"
        else:
            field += f".{part}" if field else str(part)
    return field or "$"


def _schema_error_field(error: Any) -> str:
    field = _field_path(tuple(error.absolute_path))
    if error.validator == "required":
        match = re.match(r"'([^']+)' is a required property$", error.message)
        if match:
            field = f"{field}.{match.group(1)}" if field != "$" else match.group(1)
    return field


def _schema_remediation(error: Any) -> str:
    if error.validator == "required":
        return "Add the required property named in the message."
    if error.validator == "additionalProperties":
        return "Remove the unknown property or update the canonical schema deliberately."
    if error.validator == "enum":
        return "Use one of the values declared by the canonical vocabulary."
    if error.validator == "pattern":
        return "Use a value matching the canonical ID, URI, timestamp, or hash pattern."
    if error.validator == "minItems":
        return "Add the minimum required number of items."
    if error.validator == "minLength":
        return "Provide a non-empty value."
    if error.validator == "anyOf":
        return "Provide at least one of the allowed evidence or traceability bases."
    if error.validator == "type":
        return "Change the value to the type required by the schema."
    return "Correct the value to satisfy the canonical JSON Schema."


def _register_records(
    root: Path,
    records: list[Any],
    kind: str,
    path: Path,
    entries: dict[str, RecordEntry],
    findings: list[Finding],
    *,
    line: int | None = None,
) -> None:
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        record_id = record.get("id")
        if not isinstance(record_id, str):
            continue
        existing = entries.get(record_id)
        if existing:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "DUPLICATE-ID",
                    f"ID {record_id!r} is already registered as {existing[0]}",
                    line=line,
                    field=f"{kind}[{index}].id" if line is None else "id",
                    remediation="Assign a unique canonical ID and update all downstream references.",
                )
            )
            continue
        entries[record_id] = (kind, path, line, record)


def _record_path(root: Path, path: Path) -> str:
    return _relative_path(root, path)


def _check_references(
    root: Path,
    entries: dict[str, RecordEntry],
    project_ids: set[str],
    findings: list[Finding],
) -> None:
    for record_id, (kind, path, line, record) in entries.items():
        for field, target_kind in REFERENCE_FIELDS.get(kind, []):
            raw_value = record.get(field)
            if target_kind == "requirement" and not isinstance(raw_value, list):
                references = [(None, raw_value)]
            elif isinstance(raw_value, list):
                references = list(enumerate(raw_value))
            else:
                continue
            for index, reference in references:
                if not isinstance(reference, str):
                    continue
                valid = reference in project_ids if target_kind == "project" else reference in entries and entries[reference][0] == target_kind
                if valid:
                    continue
                suffix = f"[{index}]" if index is not None else ""
                actual = entries[reference][0] if reference in entries else "missing"
                findings.append(
                    Finding(
                        _record_path(root, path),
                        "CROSS-REFERENCE",
                        f"{record_id}.{field}{suffix} references {reference!r} ({target_kind}; {actual})",
                        line=line,
                        field=f"{record_id}.{field}{suffix}",
                        remediation="Create the referenced canonical object in this repository or correct the ID.",
                    )
                )


def _check_bidirectional_decision_registries(
    root: Path,
    entries: dict[str, RecordEntry],
    findings: list[Finding],
) -> None:
    """Require typed rejected-option/uncertainty records to point both ways."""
    registry_fields = {
        "rejected-option": ("rejected_option_ids", "title"),
        "uncertainty": ("uncertainty_ids", "statement"),
    }
    for record_id, (kind, path, line, record) in entries.items():
        if kind in registry_fields:
            decision_ids = record.get("decision_ids")
            if not isinstance(decision_ids, list) or not decision_ids:
                continue
            decision_field = registry_fields[kind][0]
            for decision_id in decision_ids:
                decision_entry = entries.get(decision_id)
                if not decision_entry or decision_entry[0] != "decision":
                    continue
                reverse_ids = decision_entry[3].get(decision_field, [])
                if isinstance(reverse_ids, list) and record_id in reverse_ids:
                    continue
                findings.append(
                    Finding(
                        _record_path(root, path),
                        "DECISION-REGISTRY-REVERSE",
                        f"{record_id}.decision_ids references {decision_id!r} but the decision does not reference {record_id!r}",
                        line=line,
                        field=f"{record_id}.decision_ids",
                        remediation=f"Add {record_id} to {decision_id}.{decision_field} or remove the forward reference.",
                    )
                )
        elif kind == "decision":
            for decision_field, registry_kind in (("rejected_option_ids", "rejected-option"), ("uncertainty_ids", "uncertainty")):
                registry_ids = record.get(decision_field)
                if not isinstance(registry_ids, list):
                    continue
                for registry_id in registry_ids:
                    registry_entry = entries.get(registry_id)
                    if not registry_entry or registry_entry[0] != registry_kind:
                        continue
                    reverse_ids = registry_entry[3].get("decision_ids", [])
                    if isinstance(reverse_ids, list) and record_id in reverse_ids:
                        continue
                    findings.append(
                        Finding(
                            _record_path(root, path),
                            "DECISION-REGISTRY-REVERSE",
                            f"{record_id}.{decision_field} references {registry_id!r} but the registry record does not reference {record_id!r}",
                            line=line,
                            field=f"{record_id}.{decision_field}",
                            remediation=f"Add {record_id} to {registry_id}.decision_ids or remove the reverse reference.",
                        )
                    )


def _check_question_terminality(
    root: Path,
    entries: dict[str, RecordEntry],
    vocab: dict[str, Any],
    findings: list[Finding],
    project_status: str | None = None,
) -> None:
    """A question is asked before it is answered, so "still open" is only wrong once the project claims to be finished."""
    statuses = set(vocab.get("question_statuses", []))
    terminal_statuses = set(vocab.get("question_terminal_statuses", []))
    settled = project_status in set(vocab.get("terminal_statuses", []))
    for record_id, (kind, path, line, record) in entries.items():
        if kind != "question":
            continue
        status = record.get("status")
        if status not in statuses:
            findings.append(
                Finding(
                    _record_path(root, path),
                    "QUESTION-STATUS",
                    f"invalid question status {status!r}",
                    line=line,
                    field=f"{record_id}.status",
                    remediation="Use a question status from config/vocabularies.yaml.",
                )
            )
        if settled and record.get("priority") == "mandatory" and status not in terminal_statuses:
            findings.append(
                Finding(
                    _record_path(root, path),
                    "QUESTION-TERMINAL",
                    f"mandatory question {record_id} is not terminal: {status!r}",
                    line=line,
                    field=f"{record_id}.status",
                    remediation="Resolve the question with ANSWERED, UNRESOLVED, BLOCKED, or CANCELLED and record the reason where needed.",
                )
            )


def _check_requirement_tests(
    root: Path,
    entries: dict[str, RecordEntry],
    findings: list[Finding],
) -> None:
    for record_id, (kind, path, line, record) in entries.items():
        if kind == "requirement":
            if record.get("priority") != "mandatory" or record.get("status") in {"REJECTED", "INVALIDATED"}:
                continue
            test_ids = record.get("acceptance_test_ids") if isinstance(record.get("acceptance_test_ids"), list) else []
            tests = [
                entries[test_id][3]
                for test_id in test_ids
                if isinstance(test_id, str)
                and test_id in entries
                and entries[test_id][0] == "acceptance_test"
                and entries[test_id][3].get("target_requirement") == record_id
            ]
            if not tests:
                findings.append(
                    Finding(
                        _record_path(root, path),
                        "REQUIREMENT-TEST",
                        f"mandatory requirement {record_id} has no resolved acceptance test",
                        line=line,
                        field=f"{record_id}.acceptance_test_ids",
                        remediation="Add an acceptance test whose target_requirement is this requirement.",
                    )
                )
        elif kind == "acceptance_test":
            target = record.get("target_requirement")
            if not isinstance(target, str) or target not in entries or entries[target][0] != "requirement":
                continue
            declared_tests = entries[target][3].get("acceptance_test_ids", [])
            if record_id not in declared_tests:
                findings.append(
                    Finding(
                        _record_path(root, path),
                        "CROSS-REFERENCE",
                        f"acceptance test {record_id} is not declared by requirement {target}",
                        line=line,
                        field=f"{record_id}.target_requirement",
                        remediation="Add this acceptance-test ID to the requirement or point the test at the correct requirement.",
                    )
                )


def _check_claim_cycles(root: Path, entries: dict[str, RecordEntry], findings: list[Finding]) -> None:
    claims = {record_id: entry for record_id, entry in entries.items() if entry[0] == "claim"}
    colors: dict[str, int] = {}
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()

    def visit(record_id: str) -> None:
        colors[record_id] = 1
        stack.append(record_id)
        record = claims[record_id][3]
        for target in record.get("supporting_claims", []):
            if target not in claims:
                continue
            if colors.get(target, 0) == 0:
                visit(target)
            elif colors.get(target) == 1:
                cycle = tuple(stack[stack.index(target) :] + [target])
                if cycle not in reported:
                    reported.add(cycle)
                    path = claims[record_id][1]
                    findings.append(
                        Finding(
                            _record_path(root, path),
                            "DEPENDENCY-CYCLE",
                            f"supporting claim cycle detected: {' -> '.join(cycle)}",
                            line=claims[record_id][2],
                            field=f"{record_id}.supporting_claims",
                            remediation="Break the cycle and retain the upstream claim as an explicit evidence-backed basis.",
                        )
                    )
        stack.pop()
        colors[record_id] = 2

    for record_id in sorted(claims):
        if colors.get(record_id, 0) == 0:
            visit(record_id)


HANDOFF_MODE = "PRODUCTION_HANDOFF"
HANDOFF_REQUIRED_FILES = (
    "04_decisions/production-hypotheses.yaml",
    "04_decisions/hypothesis-comparison.yaml",
    "05_production/prototype-plans.yaml",
    "05_production/production-handoff.yaml",
    "05_production/visual-language.yaml",
    "06_governance/production-change-requests.yaml",
    "07_runtime/production-feedback-imports.jsonl",
)
HANDOFF_PATH = "05_production/production-handoff.yaml"
HYPOTHESES_PATH = "04_decisions/production-hypotheses.yaml"
COMPARISON_PATH = "04_decisions/hypothesis-comparison.yaml"
PROTOTYPES_PATH = "05_production/prototype-plans.yaml"
CREATIVE_DIRECTION_PATH = "05_production/creative-direction.md"
FEEDBACK_PATH = "07_runtime/production-feedback-imports.jsonl"
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'(=:])(?:/(?!/)\S+|[A-Za-z]:[\\/]\S*|~[\\/]\S*|file://\S+)",
    re.IGNORECASE,
)
PROHIBITED_CLASSIFICATION_PATTERN = re.compile(r"(?<![A-Z0-9_])(?:PRIVATE_RAW|RESTRICTED)(?![A-Z0-9_])")


def _handoff_finding(
    root: Path,
    project: Path,
    relative: str,
    rule: str,
    message: str,
    *,
    field: str | None = None,
    remediation: str,
) -> Finding:
    return Finding(
        _relative_path(root, project / relative),
        rule,
        message,
        field=field,
        remediation=remediation,
    )


def _iter_string_values(value: Any, path: str = "$") -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            values.extend(_iter_string_values(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            values.extend(_iter_string_values(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        values.append((path, value))
    return values


def _entry_matches(entries: dict[str, RecordEntry], record_id: Any, kind: str) -> bool:
    return isinstance(record_id, str) and record_id in entries and entries[record_id][0] == kind


def _check_handoff_reference(
    root: Path,
    project: Path,
    entries: dict[str, RecordEntry],
    record_id: Any,
    kind: str,
    *,
    relative: str,
    field: str,
    findings: list[Finding],
) -> bool:
    if _entry_matches(entries, record_id, kind):
        return True
    actual = entries[record_id][0] if isinstance(record_id, str) and record_id in entries else "missing"
    findings.append(
        _handoff_finding(
            root,
            project,
            relative,
            "HANDOFF-REFERENCE",
            f"{field} references {record_id!r} ({kind}; {actual})",
            field=field,
            remediation="Create the referenced canonical record in this project or correct the handoff ID.",
        )
    )
    return False


def _check_prototype_dags(
    root: Path,
    project: Path,
    prototype_plans: list[dict[str, Any]],
    findings: list[Finding],
) -> None:
    for plan_index, plan in enumerate(prototype_plans):
        tasks = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
        task_map: dict[str, dict[str, Any]] = {}
        for task_index, task in enumerate(tasks):
            if not isinstance(task, dict) or not isinstance(task.get("id"), str):
                continue
            task_id = task["id"]
            if task_id in task_map:
                findings.append(
                    _handoff_finding(
                        root,
                        project,
                        PROTOTYPES_PATH,
                        "PROTOTYPE-DAG",
                        f"prototype plan {plan.get('id', plan_index)!r} declares duplicate task {task_id!r}",
                        field=f"prototype_plans[{plan_index}].tasks[{task_index}].id",
                        remediation="Give every prototype task a unique project-scoped PT ID.",
                    )
                )
                continue
            task_map[task_id] = task

        for task_index, task in enumerate(tasks):
            if not isinstance(task, dict):
                continue
            task_id = task.get("id", task_index)
            dependencies = task.get("depends_on") if isinstance(task.get("depends_on"), list) else []
            for dependency_index, dependency in enumerate(dependencies):
                if dependency not in task_map:
                    findings.append(
                        _handoff_finding(
                            root,
                            project,
                            PROTOTYPES_PATH,
                            "PROTOTYPE-DAG",
                            f"task {task_id!r} depends on missing task {dependency!r}",
                            field=f"prototype_plans[{plan_index}].tasks[{task_index}].depends_on[{dependency_index}]",
                            remediation="Declare the dependency in the same prototype plan or remove the stale ID.",
                        )
                    )

        colors: dict[str, int] = {}
        stack: list[str] = []
        reported: set[tuple[str, ...]] = set()

        def visit(task_id: str) -> None:
            colors[task_id] = 1
            stack.append(task_id)
            task = task_map[task_id]
            for dependency in task.get("depends_on", []):
                if dependency not in task_map:
                    continue
                if colors.get(dependency, 0) == 0:
                    visit(dependency)
                elif colors.get(dependency) == 1:
                    cycle = tuple(stack[stack.index(dependency) :] + [dependency])
                    if cycle not in reported:
                        reported.add(cycle)
                        findings.append(
                            _handoff_finding(
                                root,
                                project,
                                PROTOTYPES_PATH,
                                "PROTOTYPE-DAG",
                                f"prototype task dependency cycle detected: {' -> '.join(cycle)}",
                                field=f"prototype_plans[{plan_index}].tasks",
                                remediation="Break the dependency cycle so prototype tasks form a directed acyclic graph.",
                            )
                        )
            stack.pop()
            colors[task_id] = 2

        for task_id in sorted(task_map):
            if colors.get(task_id, 0) == 0:
                visit(task_id)


def _check_external_schema_compatibility(
    root: Path,
    project: Path,
    feedback_records: list[tuple[int, dict[str, Any]]],
    policy: dict[str, Any],
    findings: list[Finding],
) -> None:
    if not feedback_records:
        return
    configured_path = policy.get("production_result_schema_path")
    schema_path = root / configured_path if isinstance(configured_path, str) else root / "schemas/external/production-result.v1.schema.json"
    if not schema_path.is_file():
        findings.append(
            _handoff_finding(
                root,
                project,
                FEEDBACK_PATH,
                "EXTERNAL-SCHEMA",
                "production feedback is present but the production-owned result schema snapshot is unavailable",
                field="schema_version",
                remediation="Obtain the production repository's commit-pinned result schema snapshot before importing feedback; do not invent a consumer schema.",
            )
        )
        return
    try:
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except Exception as exc:
        findings.append(
            _handoff_finding(
                root,
                project,
                str(schema_path.relative_to(root)) if schema_path.is_relative_to(root) else str(schema_path),
                "EXTERNAL-SCHEMA",
                f"production result schema snapshot is invalid: {exc}",
                remediation="Restore a valid Draft 2020-12 schema snapshot from the production repository and record its source commit.",
            )
        )
        return

    supported_versions = policy.get("production_result_schema_versions", [])
    if not isinstance(supported_versions, list):
        supported_versions = []
    if not supported_versions:
        findings.append(
            _handoff_finding(
                root,
                project,
                FEEDBACK_PATH,
                "EXTERNAL-SCHEMA",
                "production feedback is present but no supported production result schema version is configured",
                field="schema_version",
                remediation="List only reviewed versions backed by immutable production-owned schema snapshots in config/handoff-policy.yaml.",
            )
        )
        return
    for line, record in feedback_records:
        version = record.get("schema_version")
        if supported_versions and version not in supported_versions:
            findings.append(
                Finding(
                    _relative_path(root, project / FEEDBACK_PATH),
                    "EXTERNAL-SCHEMA",
                    f"unsupported production result schema version {version!r}; available versions: {', '.join(str(item) for item in supported_versions)}",
                    line=line,
                    field="schema_version",
                    remediation="Use a result version listed in config/handoff-policy.yaml or add its immutable production-owned snapshot after review.",
                )
            )
        for error in validator.iter_errors(record):
            findings.append(
                Finding(
                    _relative_path(root, project / FEEDBACK_PATH),
                    "EXTERNAL-SCHEMA",
                    error.message,
                    line=line,
                    field=_schema_error_field(error),
                    remediation="Correct the production result to the commit-pinned production-owned schema before import.",
                )
            )


def _check_handoff_security(
    root: Path,
    project: Path,
    handoff: dict[str, Any],
    access: dict[str, Any],
    policy: dict[str, Any],
    findings: list[Finding],
) -> None:
    payload = handoff_hash_payload(handoff)
    payload_bytes = canonical_json_bytes(payload)
    max_payload = policy.get("max_payload_bytes", 262144)
    if type(max_payload) is not int or max_payload <= 0:
        findings.append(
            Finding(
                "config/handoff-policy.yaml",
                "HANDOFF-SECURITY",
                "max_payload_bytes must be a positive integer",
                field="max_payload_bytes",
                remediation="Set a positive bounded payload size before validating or exporting a handoff.",
            )
        )
    elif len(payload_bytes) > max_payload:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-SECURITY",
                f"canonical handoff payload is {len(payload_bytes)} bytes, above the {max_payload}-byte limit",
                field="integrity",
                remediation="Keep the handoff to concise, public metadata and move raw evidence or large assets behind approved references.",
            )
        )

    secret_patterns: list[tuple[str, re.Pattern[str]]] = []
    for pattern in access.get("secret_patterns", []) if isinstance(access, dict) else []:
        if not isinstance(pattern, dict) or not isinstance(pattern.get("id"), str) or not isinstance(pattern.get("pattern"), str):
            continue
        try:
            secret_patterns.append((pattern["id"], re.compile(pattern["pattern"])))
        except re.error:
            continue
    signed_markers = [str(marker).lower() for marker in policy.get("signed_url_markers", []) if isinstance(marker, str)]
    for field, value in _iter_string_values(handoff):
        upper_value = value.upper()
        if PROHIBITED_CLASSIFICATION_PATTERN.search(upper_value):
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HANDOFF_PATH,
                    "HANDOFF-SECURITY",
                    "handoff contains a Git-prohibited classification or private-data marker",
                    field=field,
                    remediation="Remove PRIVATE_RAW and RESTRICTED material; retain only approved public or project-internal derived metadata.",
                )
            )
        if ABSOLUTE_PATH_PATTERN.search(value) or "../" in value or "..\\" in value:
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HANDOFF_PATH,
                    "HANDOFF-SECURITY",
                    "handoff contains an absolute or traversal path",
                    field=field,
                    remediation="Use a project-relative canonical reference or an approved opaque external URI without local path details.",
                )
            )
        lowered = value.lower()
        if "http://" in lowered or "https://" in lowered:
            if any(marker in lowered for marker in signed_markers):
                findings.append(
                    _handoff_finding(
                        root,
                        project,
                        HANDOFF_PATH,
                        "HANDOFF-SECURITY",
                        "handoff contains a signed or credential-bearing URL",
                        field=field,
                        remediation="Replace signed URLs with a stable approved URI and content hash; never persist access tokens in the handoff.",
                    )
                )
        for pattern_id, regex in secret_patterns:
            if regex.search(value):
                findings.append(
                    _handoff_finding(
                        root,
                        project,
                        HANDOFF_PATH,
                        "HANDOFF-SECURITY",
                        f"handoff value matches secret pattern {pattern_id}",
                        field=field,
                        remediation="Remove the secret and rotate it in its source system before regenerating the handoff.",
                )
            )


def _visual_finding(
    root: Path,
    project: Path,
    rule: str,
    message: str,
    *,
    field: str | None = None,
    remediation: str,
) -> Finding:
    return Finding(
        _relative_path(root, project / "05_production" / "visual-language.yaml"),
        rule,
        message,
        field=field,
        remediation=remediation,
    )


def _check_visual_language(
    root: Path,
    project: Path,
    manifest: Any,
    entries: dict[str, RecordEntry],
    visual_language: Any,
    vocab: dict[str, Any],
    findings: list[Finding],
) -> None:
    """Validate visual-language semantics without inferring a medium."""
    if not isinstance(visual_language, dict):
        return
    project_data = manifest.get("project") if isinstance(manifest, dict) else {}
    status = project_data.get("status") if isinstance(project_data, dict) else None
    statuses = vocab.get("project_statuses", [])
    medium_types = set(vocab.get("medium_types", []))
    ready_index = statuses.index("READY_FOR_PRODUCTION") if "READY_FOR_PRODUCTION" in statuses else None
    status_index = statuses.index(status) if status in statuses else None
    ready_or_later = ready_index is not None and status_index is not None and status_index >= ready_index
    medium = visual_language.get("medium")
    needs_decision = ready_or_later or medium is not None

    def finding(rule: str, message: str, *, field: str | None = None, remediation: str) -> None:
        findings.append(_visual_finding(root, project, rule, message, field=field, remediation=remediation))

    def unique(values: Any, field: str) -> list[str]:
        if not isinstance(values, list):
            return []
        strings = [item for item in values if isinstance(item, str)]
        if len(strings) != len(set(strings)):
            finding(
                "VISUAL-LANGUAGE-DUPLICATE",
                f"{field} contains duplicate values",
                field=field,
                remediation="Keep each visual-language ID or expression only once.",
            )
        return strings

    def decision_reference(record_id: Any, field: str) -> None:
        if not isinstance(record_id, str):
            return
        entry = entries.get(record_id)
        if not entry or entry[0] != "decision":
            finding(
                "VISUAL-LANGUAGE-REFERENCE",
                f"{field} references missing decision {record_id!r}",
                field=field,
                remediation="Reference an existing DC### decision from this project.",
            )
        elif entry[3].get("status") != "ADOPTED":
            finding(
                "VISUAL-LANGUAGE-REFERENCE",
                f"{field} references decision {record_id!r}, which is not ADOPTED",
                field=field,
                remediation="Use an ADOPTED decision as visual-language provenance.",
            )

    def requirement_reference(record_id: Any, field: str) -> None:
        if not isinstance(record_id, str):
            return
        entry = entries.get(record_id)
        if not entry or entry[0] != "requirement":
            finding(
                "VISUAL-LANGUAGE-REFERENCE",
                f"{field} references missing requirement {record_id!r}",
                field=field,
                remediation="Reference an existing RQ### production requirement from this project.",
            )

    def decision_candidates() -> list[tuple[str, dict[str, Any]]]:
        result: list[tuple[str, dict[str, Any]]] = []
        for record_id, (kind, _, _, record) in entries.items():
            if kind != "decision":
                continue
            question = record.get("question")
            if isinstance(question, str) and re.search(r"medium|媒介|媒体", question, re.IGNORECASE):
                result.append((record_id, record))
        return result

    if needs_decision:
        candidates = decision_candidates()
        valid_candidate = len(candidates) == 1 and candidates[0][1].get("selected_option") in medium_types
        if len(candidates) != 1 or not valid_candidate or candidates[0][1].get("status") != "ADOPTED":
            finding(
                "VISUAL-LANGUAGE-MEDIUM-DECISION",
                "visual language requires exactly one ADOPTED decision with a configured medium selected_option",
                field="medium/source_decision_id",
                remediation="Record one explicit medium-selection question, select one medium_types value, and mark that DC decision ADOPTED.",
            )
        elif isinstance(medium, dict):
            candidate_id, candidate = candidates[0]
            if medium.get("source_decision_id") != candidate_id or medium.get("primary") != candidate.get("selected_option"):
                finding(
                    "VISUAL-LANGUAGE-MEDIUM-DECISION",
                    "visual language medium does not match the unique adopted medium decision",
                    field="medium",
                    remediation="Copy selected_option and the decision ID from the adopted medium decision.",
                )

    if isinstance(medium, dict):
        decision_reference(medium.get("source_decision_id"), "medium.source_decision_id")

    techniques = visual_language.get("techniques")
    technique_ids: list[str] = []
    if isinstance(techniques, list):
        for index, technique in enumerate(techniques):
            if not isinstance(technique, dict):
                continue
            technique_id = technique.get("id")
            if isinstance(technique_id, str):
                technique_ids.append(technique_id)
            for decision_index, decision_id in enumerate(technique.get("source_decision_ids", [])):
                decision_reference(decision_id, f"techniques[{index}].source_decision_ids[{decision_index}]")
            for requirement_index, requirement_id in enumerate(technique.get("requirement_ids", [])):
                requirement_reference(requirement_id, f"techniques[{index}].requirement_ids[{requirement_index}]")
    unique(technique_ids, "techniques.id")

    def check_condition(name: str) -> None:
        condition = visual_language.get(name)
        if not isinstance(condition, dict):
            return
        preferred = unique(condition.get("preferred"), f"{name}.preferred")
        prohibited = unique(condition.get("prohibited"), f"{name}.prohibited")
        source_decisions = unique(condition.get("source_decision_ids"), f"{name}.source_decision_ids")
        overlap = sorted(set(preferred).intersection(prohibited))
        if overlap:
            finding(
                "VISUAL-LANGUAGE-DUPLICATE",
                f"{name} places the same expression in preferred and prohibited: {', '.join(overlap)}",
                field=name,
                remediation="Keep preferred and prohibited expressions disjoint.",
            )
        for index, decision_id in enumerate(source_decisions):
            decision_reference(decision_id, f"{name}.source_decision_ids[{index}]")
        applicability = condition.get("applicability")
        rationale = condition.get("rationale")
        complete_condition = ready_or_later or medium is not None
        if applicability == "APPLICABLE":
            if complete_condition and not preferred:
                finding(
                    "VISUAL-LANGUAGE-COMPLETENESS",
                    f"{name} marked APPLICABLE must declare at least one preferred expression",
                    field=f"{name}.preferred",
                    remediation="Add a concrete preferred expression or mark the section NOT_APPLICABLE.",
                )
            if complete_condition and (not isinstance(rationale, str) or not rationale.strip()):
                finding(
                    "VISUAL-LANGUAGE-COMPLETENESS",
                    f"{name} marked APPLICABLE must include a rationale",
                    field=f"{name}.rationale",
                    remediation="Explain the research reason for the applicable visual condition.",
                )
            if complete_condition and not source_decisions:
                finding(
                    "VISUAL-LANGUAGE-COMPLETENESS",
                    f"{name} marked APPLICABLE must cite at least one source decision",
                    field=f"{name}.source_decision_ids",
                    remediation="Reference the ADOPTED decision that establishes this condition.",
                )
        elif applicability == "NOT_APPLICABLE":
            if preferred or prohibited or source_decisions:
                finding(
                    "VISUAL-LANGUAGE-APPLICABILITY",
                    f"{name} marked NOT_APPLICABLE must have empty preferred, prohibited, and source_decision_ids",
                    field=name,
                    remediation="Clear the condition arrays or mark the section APPLICABLE.",
                )
            if complete_condition and (not isinstance(rationale, str) or not rationale.strip()):
                finding(
                    "VISUAL-LANGUAGE-COMPLETENESS",
                    f"{name} marked NOT_APPLICABLE must include a rationale",
                    field=f"{name}.rationale",
                    remediation="Record why this visual condition does not apply.",
                )

    check_condition("palette")
    check_condition("composition")

    prohibited = visual_language.get("prohibited_expressions")
    prohibited_ids: list[str] = []
    if isinstance(prohibited, list):
        for index, expression in enumerate(prohibited):
            if not isinstance(expression, dict):
                continue
            expression_id = expression.get("id")
            if isinstance(expression_id, str):
                prohibited_ids.append(expression_id)
            for decision_index, decision_id in enumerate(expression.get("source_decision_ids", [])):
                decision_reference(decision_id, f"prohibited_expressions[{index}].source_decision_ids[{decision_index}]")
            for requirement_index, requirement_id in enumerate(expression.get("requirement_ids", [])):
                requirement_reference(requirement_id, f"prohibited_expressions[{index}].requirement_ids[{requirement_index}]")
    unique(prohibited_ids, "prohibited_expressions.id")
    if ready_or_later and not prohibited_ids:
        finding(
            "VISUAL-LANGUAGE-COMPLETENESS",
            "READY_FOR_PRODUCTION visual language requires at least one prohibited expression",
            field="prohibited_expressions",
            remediation="Record at least one evidence-backed prohibited expression.",
        )

    uncertainty_ids = unique(visual_language.get("unresolved_uncertainty_ids"), "unresolved_uncertainty_ids")
    for index, uncertainty_id in enumerate(uncertainty_ids):
        entry = entries.get(uncertainty_id)
        if not entry or entry[0] != "uncertainty":
            finding(
                "VISUAL-LANGUAGE-REFERENCE",
                f"unresolved_uncertainty_ids references missing uncertainty {uncertainty_id!r}",
                field=f"unresolved_uncertainty_ids[{index}]",
                remediation="Reference an existing U### uncertainty record.",
            )
        elif entry[3].get("status") not in set(vocab.get("uncertainty_statuses", [])):
            finding(
                "VISUAL-LANGUAGE-REFERENCE",
                f"uncertainty {uncertainty_id!r} must have OPEN or ACCEPTED_RISK status",
                field=f"unresolved_uncertainty_ids[{index}]",
                remediation="Set the referenced uncertainty status to OPEN or ACCEPTED_RISK.",
            )

    if ready_or_later:
        if not isinstance(medium, dict):
            finding(
                "VISUAL-LANGUAGE-COMPLETENESS",
                "READY_FOR_PRODUCTION visual language requires a medium record",
                field="medium",
                remediation="Populate medium from the unique ADOPTED medium decision.",
            )
        if not isinstance(techniques, list) or not techniques:
            finding(
                "VISUAL-LANGUAGE-COMPLETENESS",
                "READY_FOR_PRODUCTION visual language requires at least one technique",
                field="techniques",
                remediation="Record at least one technique with decision provenance.",
            )


def _check_handoff_contract(
    root: Path,
    project: Path,
    manifest: Any,
    entries: dict[str, RecordEntry],
    extension_values: dict[str, Any],
    handoff: Any,
    access: dict[str, Any],
    policy: dict[str, Any],
    feedback_records: list[tuple[int, dict[str, Any]]],
    findings: list[Finding],
) -> None:
    if not isinstance(manifest, dict) or manifest.get("workflow_mode", "RESEARCH_ONLY") != HANDOFF_MODE:
        return

    for relative in HANDOFF_REQUIRED_FILES:
        if not (project / relative).is_file():
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    relative,
                    "HANDOFF-STRUCTURE",
                    "production handoff workflow requires this file",
                    remediation="Create the file from templates/project or restore the manifest-declared production handoff entry point.",
                )
            )

    hypotheses = extension_values.get("production-hypothesis", [])
    comparisons = extension_values.get("hypothesis-comparison", [])
    prototype_plans = extension_values.get("prototype-plan", [])
    if not isinstance(hypotheses, list):
        hypotheses = []
    if not isinstance(comparisons, list):
        comparisons = []
    if not isinstance(prototype_plans, list):
        prototype_plans = []

    prototype_by_id = {
        plan.get("id"): plan
        for plan in prototype_plans
        if isinstance(plan, dict) and isinstance(plan.get("id"), str)
    }

    if not hypotheses:
        findings.append(
            _handoff_finding(
                root,
                project,
                HYPOTHESES_PATH,
                "HYPOTHESIS-TRACE",
                "production handoff workflow requires at least one production hypothesis",
                field="hypotheses",
                remediation="Create at least one schema-valid hypothesis grounded in an adopted decision and insight.",
            )
        )

    hypothesis_ids = {record.get("id") for record in hypotheses if isinstance(record, dict) and isinstance(record.get("id"), str)}
    for hypothesis_index, hypothesis in enumerate(hypotheses):
        if not isinstance(hypothesis, dict):
            continue
        for decision_id in hypothesis.get("source_decision_ids", []):
            decision_entry = entries.get(decision_id) if isinstance(decision_id, str) else None
            if decision_entry and decision_entry[0] == "decision" and decision_entry[3].get("status") != "ADOPTED":
                findings.append(
                    _handoff_finding(
                        root,
                        project,
                        HYPOTHESES_PATH,
                        "HYPOTHESIS-TRACE",
                        f"hypothesis {hypothesis.get('id', hypothesis_index)!r} cites decision {decision_id!r}, which is not ADOPTED",
                        field=f"hypotheses[{hypothesis_index}].source_decision_ids",
                        remediation="Ground the hypothesis in an ADOPTED decision or revise the decision lifecycle before handoff.",
                    )
                )
        for uncertainty_index, uncertainty in enumerate(hypothesis.get("uncertainties", [])):
            if not isinstance(uncertainty, dict):
                continue
            severity = uncertainty.get("severity")
            plan_ids = uncertainty.get("prototype_plan_ids") if isinstance(uncertainty.get("prototype_plan_ids"), list) else []
            external_reason = uncertainty.get("external_validation_reason")
            if severity in {"MAJOR", "CRITICAL"} and not plan_ids and (
                not isinstance(external_reason, str) or not external_reason.strip()
            ):
                findings.append(
                    _handoff_finding(
                        root,
                        project,
                        HYPOTHESES_PATH,
                        "UNCERTAINTY-PROTOTYPE",
                        f"{severity} uncertainty {uncertainty.get('id', uncertainty_index)!r} has no prototype plan or external validation basis",
                        field=f"hypotheses[{hypothesis_index}].uncertainties[{uncertainty_index}].prototype_plan_ids",
                        remediation="Connect the uncertainty to a Prototype Plan that tests it, or record an explicit external-validation basis.",
                    )
                )
            uncertainty_id = uncertainty.get("id")
            hypothesis_id = hypothesis.get("id")
            for plan_index, plan_id in enumerate(plan_ids):
                plan = prototype_by_id.get(plan_id)
                if not isinstance(plan, dict):
                    findings.append(
                        _handoff_finding(
                            root,
                            project,
                            HYPOTHESES_PATH,
                            "UNCERTAINTY-PROTOTYPE",
                            f"uncertainty {uncertainty_id!r} references missing Prototype Plan {plan_id!r}",
                            field=f"hypotheses[{hypothesis_index}].uncertainties[{uncertainty_index}].prototype_plan_ids[{plan_index}]",
                            remediation="Create the referenced Prototype Plan or remove the stale plan ID.",
                        )
                    )
                    continue
                if plan.get("hypothesis_id") != hypothesis_id or uncertainty_id not in plan.get("uncertainty_ids", []):
                    findings.append(
                        _handoff_finding(
                            root,
                            project,
                            HYPOTHESES_PATH,
                            "UNCERTAINTY-PROTOTYPE",
                            f"uncertainty {uncertainty_id!r} and Prototype Plan {plan_id!r} do not reference each other within hypothesis {hypothesis_id!r}",
                            field=f"hypotheses[{hypothesis_index}].uncertainties[{uncertainty_index}].prototype_plan_ids[{plan_index}]",
                            remediation="Make the hypothesis, uncertainty, and Prototype Plan references mutually consistent.",
                        )
                    )

    if len(hypotheses) == 1:
        rationale = hypotheses[0].get("single_hypothesis_rationale") if isinstance(hypotheses[0], dict) else None
        if not isinstance(rationale, str) or not rationale.strip():
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HYPOTHESES_PATH,
                    "HYPOTHESIS-SINGLETON",
                    "a single production hypothesis must explain why no meaningful alternative is carried forward",
                    field="hypotheses[0].single_hypothesis_rationale",
                    remediation="Record a concise rationale for the single-hypothesis decision, or generate and compare meaningful alternatives.",
                )
            )
    elif len(hypotheses) > 1:
        complete_comparisons = []
        for comparison_index, comparison in enumerate(comparisons):
            if not isinstance(comparison, dict) or set(comparison.get("hypothesis_ids", [])) != hypothesis_ids:
                continue
            axis_sets = []
            for axis in comparison.get("axes", []):
                if isinstance(axis, dict):
                    axis_sets.append({item.get("hypothesis_id") for item in axis.get("assessments", []) if isinstance(item, dict)})
            if axis_sets and all(axis_set == hypothesis_ids for axis_set in axis_sets):
                if comparison.get("status") in {"COMPLETE", "HUMAN_SELECTION_REQUIRED"}:
                    complete_comparisons.append((comparison_index, comparison))
        if not complete_comparisons:
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    COMPARISON_PATH,
                    "HYPOTHESIS-COMPARISON",
                    "multiple production hypotheses require one completed common-axis comparison covering every candidate",
                    field="comparisons",
                    remediation="Compare all candidates on the same axes and mark the comparison COMPLETE or HUMAN_SELECTION_REQUIRED.",
                )
            )

    _check_prototype_dags(root, project, prototype_plans, findings)
    for plan_index, plan in enumerate(prototype_plans):
        if not isinstance(plan, dict):
            continue
        if plan.get("status") == "EXTERNAL_VALIDATION_REQUIRED" and (
            not isinstance(plan.get("external_validation_reason"), str)
            or not plan["external_validation_reason"].strip()
        ):
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    PROTOTYPES_PATH,
                    "PROTOTYPE-EXTERNAL",
                    f"prototype plan {plan.get('id', plan_index)!r} requires an external validation reason",
                    field=f"prototype_plans[{plan_index}].external_validation_reason",
                    remediation="Record why the uncertainty cannot be tested locally and identify the external validation boundary.",
                )
            )

    if not isinstance(handoff, dict) or not handoff:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-STRUCTURE",
                "PRODUCTION_HANDOFF workflow requires a non-empty production handoff object",
                field="$",
                remediation="Generate production-handoff.yaml from the canonical hypotheses, prototype plans, requirements, and source references.",
            )
        )
        _check_external_schema_compatibility(root, project, feedback_records, policy, findings)
        return

    handoff_project_id = handoff.get("research_project_id")
    manifest_project = manifest.get("project") if isinstance(manifest.get("project"), dict) else {}
    if handoff_project_id != manifest_project.get("id"):
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-REFERENCE",
                f"research_project_id {handoff_project_id!r} does not match manifest project ID {manifest_project.get('id')!r}",
                field="research_project_id",
                remediation="Generate the handoff for the same project represented by manifest.yaml.",
            )
        )
    if handoff.get("research_project_version") != manifest_project.get("version"):
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-REFERENCE",
                "research_project_version does not match manifest.project.version",
                field="research_project_version",
                remediation="Regenerate the handoff after recording the canonical project version.",
            )
        )

    selection = handoff.get("selection") if isinstance(handoff.get("selection"), dict) else {}
    selection_status = selection.get("status")
    selected_id = selection.get("selected_hypothesis_id")
    alternative_ids = selection.get("alternative_hypothesis_ids") if isinstance(selection.get("alternative_hypothesis_ids"), list) else []
    if selected_id is not None and selected_id not in hypothesis_ids:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-SELECTION",
                f"selected hypothesis {selected_id!r} is not present in production-hypotheses.yaml",
                field="selection.selected_hypothesis_id",
                remediation="Select a hypothesis declared by this project or set HUMAN_SELECTION_REQUIRED before human review.",
            )
        )
    for alternative_index, alternative_id in enumerate(alternative_ids):
        if alternative_id not in hypothesis_ids:
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HANDOFF_PATH,
                    "HANDOFF-SELECTION",
                    f"alternative hypothesis {alternative_id!r} is not present in production-hypotheses.yaml",
                    field=f"selection.alternative_hypothesis_ids[{alternative_index}]",
                    remediation="Declare the alternative candidate before including it in the handoff.",
                )
            )
    if selected_id is not None and selected_id in alternative_ids:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-SELECTION",
                "selected hypothesis cannot also be an alternative hypothesis",
                field="selection.alternative_hypothesis_ids",
                remediation="Remove the selected hypothesis from the alternatives list.",
            )
        )
    authority = selection.get("authority")
    human_approval_required = selection.get("human_approval_required")
    if selection_status == "HUMAN_SELECTION_REQUIRED" and (selected_id is not None or human_approval_required is not True):
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-SELECTION",
                "HUMAN_SELECTION_REQUIRED must not contain a selected hypothesis and must require human approval",
                field="selection",
                remediation="Clear selected_hypothesis_id and set human_approval_required to true until a human selects a candidate.",
            )
        )
    if selection_status == "HUMAN_SELECTED" and (
        not isinstance(selected_id, str) or authority != "human-approved" or human_approval_required is not False
    ):
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-SELECTION",
                "HUMAN_SELECTED requires a selected hypothesis, human-approved authority, and completed approval",
                field="selection",
                remediation="Record the human-approved authority only after the human selection is complete.",
            )
        )
    if selection_status == "AGENT_RECOMMENDED" and (
        not isinstance(selected_id, str) or authority != "agent-recommended" or human_approval_required is not False
    ):
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-SELECTION",
                "AGENT_RECOMMENDED requires a selected hypothesis, agent-recommended authority, and no pending human approval",
                field="selection.human_approval_required",
                remediation="Use HUMAN_SELECTION_REQUIRED for an unresolved human choice or clear the approval flag for an agent recommendation.",
            )
        )

    if selection_status == "HUMAN_SELECTION_REQUIRED":
        candidate_hypothesis_ids = hypothesis_ids
    elif isinstance(selected_id, str):
        candidate_hypothesis_ids = {selected_id}
    else:
        candidate_hypothesis_ids = set(alternative_ids)
    required_plan_ids = {
        plan.get("id")
        for plan in prototype_plans
        if isinstance(plan, dict)
        and isinstance(plan.get("id"), str)
        and plan.get("hypothesis_id") in candidate_hypothesis_ids
        and plan.get("status") not in {"CANCELLED", "FAILED"}
    }
    declared_plan_ids = set(handoff.get("prototype_plan_ids", []))
    missing_plan_ids = sorted(required_plan_ids - declared_plan_ids)
    if missing_plan_ids:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-REFERENCE",
                f"handoff omits Prototype Plans required by the selected candidate: {', '.join(missing_plan_ids)}",
                field="prototype_plan_ids",
                remediation="Include every active Prototype Plan attached to the selected candidate, or mark the plan cancelled with a recorded reason.",
            )
        )

    lifecycle_status = handoff.get("status")
    supersedes = handoff.get("supersedes")
    revision = handoff.get("revision")
    if supersedes == handoff.get("handoff_id"):
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-LIFECYCLE",
                "handoff cannot supersede itself",
                field="supersedes",
                remediation="Point supersedes to the prior handoff ID, not the current handoff ID.",
            )
        )
    if supersedes and isinstance(revision, int) and revision < 2:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-LIFECYCLE",
                "a handoff with supersedes must have revision 2 or later",
                field="revision",
                remediation="Increment revision when superseding an earlier handoff or clear supersedes for the initial revision.",
            )
        )
    if lifecycle_status == "READY":
        for gap_index, gap in enumerate(handoff.get("open_gaps", [])):
            if not isinstance(gap, dict):
                continue
            if gap.get("blocking") is True:
                findings.append(
                    _handoff_finding(
                        root,
                        project,
                        HANDOFF_PATH,
                        "HANDOFF-READINESS",
                        f"READY handoff contains a blocking gap {gap.get('id', gap_index)!r}",
                        field=f"open_gaps[{gap_index}]",
                        remediation="Resolve the blocking gap or use a non-ready handoff status until production can proceed safely.",
                    )
                )

    creative_ref = handoff.get("creative_direction_ref")
    if isinstance(creative_ref, str):
        creative_path = (project / creative_ref).resolve()
        if project.resolve() not in creative_path.parents or not creative_path.is_file():
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HANDOFF_PATH,
                    "HANDOFF-REFERENCE",
                    f"creative_direction_ref does not resolve to a file inside the project: {creative_ref!r}",
                    field="creative_direction_ref",
                    remediation="Use an existing project-relative creative-direction path; never use an absolute local path.",
                )
            )

    for plan_index, plan_id in enumerate(handoff.get("prototype_plan_ids", [])):
        _check_handoff_reference(
            root,
            project,
            entries,
            plan_id,
            "prototype-plan",
            relative=HANDOFF_PATH,
            field=f"prototype_plan_ids[{plan_index}]",
            findings=findings,
        )

    source_refs = handoff.get("source_refs") if isinstance(handoff.get("source_refs"), dict) else {}
    for field, kind in (("decision_ids", "decision"), ("insight_ids", "insight"), ("evidence_ids", "evidence")):
        for index, record_id in enumerate(source_refs.get(field, [])):
            _check_handoff_reference(
                root,
                project,
                entries,
                record_id,
                kind,
                relative=HANDOFF_PATH,
                field=f"source_refs.{field}[{index}]",
                findings=findings,
            )

    canonical_requirements = {record_id: entry[3] for record_id, entry in entries.items() if entry[0] == "requirement"}
    handoff_requirements = handoff.get("requirements") if isinstance(handoff.get("requirements"), list) else []
    handoff_requirement_ids: set[str] = set()
    for requirement_index, snapshot in enumerate(handoff_requirements):
        if not isinstance(snapshot, dict):
            continue
        requirement_id = snapshot.get("id")
        if isinstance(requirement_id, str):
            handoff_requirement_ids.add(requirement_id)
        source = canonical_requirements.get(requirement_id)
        if source is None:
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HANDOFF_PATH,
                    "HANDOFF-REQUIREMENT",
                    f"handoff requirement {requirement_id!r} is not present in production-requirements.yaml",
                    field=f"requirements[{requirement_index}].id",
                    remediation="Include the canonical requirement ID and regenerate the handoff snapshot.",
                )
            )
            continue
        if snapshot.get("statement") != source.get("statement") or snapshot.get("priority") != source.get("priority"):
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HANDOFF_PATH,
                    "HANDOFF-REQUIREMENT",
                    f"handoff requirement {requirement_id!r} changes the canonical statement or priority",
                    field=f"requirements[{requirement_index}]",
                    remediation="Treat production-requirements.yaml as the source of truth and regenerate the handoff snapshot.",
                )
            )
        snapshot_decisions = set(snapshot.get("source_decision_ids", []))
        canonical_decisions = set(source.get("source_decisions", []))
        snapshot_tests = set(snapshot.get("acceptance_test_ids", []))
        canonical_tests = set(source.get("acceptance_test_ids", []))
        if snapshot_decisions != canonical_decisions or snapshot_tests != canonical_tests:
            findings.append(
                _handoff_finding(
                    root,
                    project,
                    HANDOFF_PATH,
                    "HANDOFF-REQUIREMENT",
                    f"handoff requirement {requirement_id!r} changes canonical decision or acceptance-test references",
                    field=f"requirements[{requirement_index}]",
                    remediation="Copy the exact source_decisions and acceptance_test_ids from the canonical requirement before regenerating the handoff.",
                )
            )
        for test_index, test_id in enumerate(snapshot.get("acceptance_test_ids", [])):
            if not _entry_matches(entries, test_id, "acceptance_test"):
                _check_handoff_reference(
                    root,
                    project,
                    entries,
                    test_id,
                    "acceptance_test",
                    relative=HANDOFF_PATH,
                    field=f"requirements[{requirement_index}].acceptance_test_ids[{test_index}]",
                    findings=findings,
                )
            elif entries[test_id][3].get("target_requirement") != requirement_id:
                findings.append(
                    _handoff_finding(
                        root,
                        project,
                        HANDOFF_PATH,
                        "HANDOFF-REQUIREMENT",
                        f"acceptance test {test_id!r} does not target requirement {requirement_id!r}",
                        field=f"requirements[{requirement_index}].acceptance_test_ids[{test_index}]",
                        remediation="Use acceptance tests whose target_requirement matches the handoff requirement ID.",
                    )
                )
    mandatory_ids = {
        record_id
        for record_id, record in canonical_requirements.items()
        if record.get("priority") == "mandatory" and record.get("status") not in {"REJECTED", "INVALIDATED"}
    }
    missing_mandatory = sorted(mandatory_ids - handoff_requirement_ids)
    if missing_mandatory:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-REQUIREMENT",
                f"handoff omits mandatory canonical requirements: {', '.join(missing_mandatory)}",
                field="requirements",
                remediation="Include every active mandatory requirement and its acceptance tests in the handoff.",
            )
        )

    expected_hash = handoff_sha256(handoff)
    actual_hash = (handoff.get("integrity") or {}).get("content_sha256") if isinstance(handoff.get("integrity"), dict) else None
    if actual_hash != expected_hash:
        findings.append(
            _handoff_finding(
                root,
                project,
                HANDOFF_PATH,
                "HANDOFF-HASH",
                f"canonical payload hash {actual_hash!r} does not match recalculated hash {expected_hash!r}",
                field="integrity.content_sha256",
                remediation="Recalculate the hash with tools.canonical.handoff_sha256 after changing any handoff field.",
            )
        )
    _check_handoff_security(root, project, handoff, access, policy, findings)
    _check_external_schema_compatibility(root, project, feedback_records, policy, findings)


def _check_lifecycle(
    root: Path,
    manifest: Any,
    state: Any,
    completion_report: Any,
    run_events: list[tuple[int, dict[str, Any]]],
    vocab: dict[str, Any],
    project: Path,
    findings: list[Finding],
) -> None:
    statuses = set(vocab.get("project_statuses", []))
    terminal_statuses = set(vocab.get("terminal_statuses", []))
    transitions = vocab.get("project_state_transitions", {})
    if not isinstance(transitions, dict):
        transitions = {}
    manifest_project = (manifest.get("project") or {}) if isinstance(manifest, dict) else {}
    manifest_status = manifest_project.get("status") if isinstance(manifest_project, dict) else None
    state_status = state.get("status") if isinstance(state, dict) else None
    completion_status = completion_report.get("status") if isinstance(completion_report, dict) else None
    state_path = project / "07_runtime" / "research-state.json"
    report_path = project / "07_runtime" / "completion-report.json"
    if state_status not in statuses:
        findings.append(
            Finding(
                _record_path(root, state_path),
                "LIFECYCLE-STATUS",
                f"invalid runtime status {state_status!r}",
                field="status",
                remediation="Use a project status from config/vocabularies.yaml.",
            )
        )
    if manifest_status in terminal_statuses and completion_status != manifest_status:
        findings.append(
            Finding(
                _record_path(root, report_path),
                "LIFECYCLE-STATE-SYNC",
                f"terminal completion status {completion_status!r} differs from manifest status {manifest_status!r}",
                field="status",
                remediation="Set completion-report.status to the terminal manifest status.",
            )
        )

    seen_events: set[str] = set()
    previous_to: str | None = None
    for line, event in run_events:
        event_id = event.get("event_id", event.get("id"))
        if isinstance(event_id, str):
            if event_id in seen_events:
                findings.append(
                    Finding(
                        _record_path(root, project / "07_runtime" / "run-log.jsonl"),
                        "DUPLICATE-EVENT-ID",
                        f"run event ID {event_id!r} occurs more than once",
                        line=line,
                        field="event_id",
                        remediation="Assign a unique event ID to every run-log event.",
                    )
                )
            seen_events.add(event_id)
        if "from_status" not in event and "to_status" not in event:
            continue
        from_status = event.get("from_status")
        to_status = event.get("to_status")
        event_path = project / "07_runtime" / "run-log.jsonl"
        if from_status not in statuses or to_status not in statuses:
            findings.append(
                Finding(
                    _record_path(root, event_path),
                    "LIFECYCLE-TRANSITION",
                    f"unknown lifecycle transition {from_status!r} -> {to_status!r}",
                    line=line,
                    field="from_status/to_status",
                    remediation="Use valid project statuses and record an explicit allowed transition.",
                )
            )
            continue
        if to_status not in set(transitions.get(from_status, [])):
            findings.append(
                Finding(
                    _record_path(root, event_path),
                    "LIFECYCLE-TRANSITION",
                    f"illegal lifecycle transition {from_status} -> {to_status}",
                    line=line,
                    field="from_status/to_status",
                    remediation="Follow config/vocabularies.yaml project_state_transitions or record a deliberate migration in the policy.",
                )
            )
        if previous_to is not None and from_status != previous_to:
            findings.append(
                Finding(
                    _record_path(root, event_path),
                    "LIFECYCLE-SEQUENCE",
                    f"transition starts at {from_status}, but the preceding event ended at {previous_to}",
                    line=line,
                    field="from_status",
                    remediation="Make run-log transition events form one contiguous status sequence.",
                )
            )
        previous_to = to_status
    if previous_to is not None and state_status != previous_to:
        findings.append(
            Finding(
                _record_path(root, state_path),
                "LIFECYCLE-STATE-SYNC",
                f"runtime status {state_status!r} differs from the last run event status {previous_to!r}",
                field="status",
                remediation="Update research-state.json to the last applied transition or record the missing event.",
            )
        )


def _schema_findings(
    root: Path,
    path: Path,
    validator: Draft202012Validator,
    instance: Any,
    *,
    line: int | None = None,
    field_prefix: str | None = None,
) -> list[Finding]:
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.validator or ""),
    )
    findings: list[Finding] = []
    for error in errors:
        field = _schema_error_field(error)
        if field_prefix:
            field = field_prefix if field == "$" else f"{field_prefix}{field[1:]}"
        findings.append(
            Finding(
                _relative_path(root, path),
                f"SCHEMA:{error.validator or 'validation'}",
                error.message,
                line=line,
                field=field,
                remediation=_schema_remediation(error),
            )
        )
    return findings


def _load_schema_validators(root: Path, findings: list[Finding]) -> dict[str, Draft202012Validator]:
    documents: dict[str, dict[str, Any]] = {}
    schemas_root = root / "schemas"
    for path in sorted(schemas_root.glob("*.json")):
        try:
            document = load_json(path)
        except Exception as exc:
            findings.append(_exception_finding(root, path, "SCHEMA-JSON", exc))
            continue
        if not isinstance(document, dict) or "$schema" not in document:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "SCHEMA-META",
                    "missing $schema",
                    field="$schema",
                    remediation="Declare the Draft 2020-12 meta-schema in the schema document.",
                )
            )
            continue
        try:
            Draft202012Validator.check_schema(document)
        except SchemaError as exc:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "SCHEMA-DEFINITION",
                    str(exc.message),
                    field="$",
                    remediation="Fix the schema definition before validating project data.",
                )
            )
            continue
        documents[path.name] = document

    registry = Registry().with_resources(
        [
            (document["$id"], Resource.from_contents(document))
            for document in documents.values()
            if isinstance(document.get("$id"), str)
        ]
    )
    validators: dict[str, Draft202012Validator] = {}
    for schema_name in DOMAIN_SCHEMAS:
        schema_path = schemas_root / f"{schema_name}.schema.json"
        document = documents.get(schema_path.name)
        if document is None:
            findings.append(
                Finding(
                    _relative_path(root, schema_path),
                    "SCHEMA-MISSING",
                    "canonical domain schema is unavailable",
                    remediation="Restore the required schema file and rerun validation.",
                )
            )
            continue
        try:
            validators[schema_name] = Draft202012Validator(document, registry=registry)
        except Exception as exc:
            findings.append(
                Finding(
                    _relative_path(root, schema_path),
                    "SCHEMA-REFERENCE",
                    str(exc),
                    remediation="Fix the schema reference or restore the referenced common definition.",
                )
            )
    return validators


def _validate_yaml_collection(
    root: Path,
    path: Path,
    value: Any,
    key: str,
    validator: Draft202012Validator | None,
    findings: list[Finding],
) -> None:
    relative = _relative_path(root, path)
    if not isinstance(value, dict):
        findings.append(
            Finding(
                relative,
                "STRUCTURE:type",
                "canonical YAML document must be an object",
                field="$",
                remediation=f"Wrap the records in an object containing the {key!r} list.",
            )
        )
        return
    records = value.get(key)
    if not isinstance(records, list):
        findings.append(
            Finding(
                relative,
                "STRUCTURE:type",
                f"{key} must be a list",
                field=key,
                remediation=f"Set {key} to a YAML list of canonical objects.",
            )
        )
        return
    if validator is None:
        return
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            findings.append(
                Finding(
                    relative,
                    "STRUCTURE:type",
                    f"{key}[{index}] must be an object",
                    field=f"{key}[{index}]",
                    remediation="Replace the item with a mapping/object matching the canonical schema.",
                )
            )
            continue
        findings.extend(_schema_findings(root, path, validator, record, field_prefix=f"{key}[{index}]"))


def _secret_findings(root: Path, path: Path, patterns: list[dict[str, Any]]) -> list[Finding]:
    try:
        content = path.read_bytes()
        if b"\x00" in content:
            return []
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[Finding] = []
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns:
        if not isinstance(pattern, dict) or not isinstance(pattern.get("id"), str) or not isinstance(pattern.get("pattern"), str):
            continue
        try:
            compiled.append((pattern["id"], re.compile(pattern["pattern"])))
        except re.error:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "SECURITY-PATTERN",
                    f"invalid secret pattern {pattern['id']!r}",
                    remediation="Fix the regular expression in config/access-policy.yaml.",
                )
            )
    for line_number, line in enumerate(text.splitlines(), 1):
        for pattern_id, regex in compiled:
            if regex.search(line):
                findings.append(
                    Finding(
                        _relative_path(root, path),
                        "SECRET-SCAN",
                        f"likely secret matched pattern {pattern_id}",
                        line=line_number,
                        field="$",
                        remediation="Remove the secret from the repository and rotate it in its source system.",
                    )
                )
    return findings


def _knowledge_finding(
    root: Path,
    path: Path,
    rule: str,
    message: str,
    *,
    line: int | None = None,
    field: str | None = None,
    remediation: str,
) -> Finding:
    return Finding(
        _relative_path(root, path),
        rule,
        message,
        line=line,
        field=field,
        remediation=remediation,
    )


def _check_knowledge_semantics(
    root: Path,
    entries: dict[str, RecordEntry],
    vocab: dict[str, Any],
    findings: list[Finding],
) -> None:
    """Apply the cross-record rules that JSON Schema cannot express."""
    relationship_types = set(vocab.get("relationship_types", []))
    contradiction_statuses = set(vocab.get("contradiction_statuses", []))
    knowledge_kinds = {"evidence", "observation", "claim", "external-reference"}

    for record_id, (kind, path, line, record) in entries.items():
        if kind == "relationship":
            relationship_type = record.get("type")
            if relationship_type not in relationship_types:
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "KNOWLEDGE-VOCABULARY",
                        f"relationship type {relationship_type!r} is not declared in config/vocabularies.yaml",
                        line=line,
                        field="type",
                        remediation="Use a value from config/vocabularies.yaml#relationship_types.",
                    )
                )
            from_id = record.get("from_id")
            to_id = record.get("to_id")
            if isinstance(from_id, str) and isinstance(to_id, str) and from_id == to_id:
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "RELATIONSHIP-SELF",
                        f"relationship {record_id} cannot reference the same endpoint {from_id!r} twice",
                        line=line,
                        field="from_id/to_id",
                        remediation="Use two distinct EV, OB, CL, or XR endpoint IDs.",
                    )
                )
            for field in ("from_id", "to_id"):
                reference = record.get(field)
                if not isinstance(reference, str):
                    continue
                target = entries.get(reference)
                if target and target[0] in knowledge_kinds:
                    continue
                actual = target[0] if target else "missing"
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "CROSS-REFERENCE",
                        f"{record_id}.{field} references {reference!r} (knowledge endpoint; {actual})",
                        line=line,
                        field=field,
                        remediation="Create the referenced evidence, observation, claim, or external-reference in this project.",
                    )
                )
        elif kind == "contradiction":
            status = record.get("status")
            if status not in contradiction_statuses:
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "KNOWLEDGE-VOCABULARY",
                        f"contradiction status {status!r} is not declared in config/vocabularies.yaml",
                        line=line,
                        field="status",
                        remediation="Use a value from config/vocabularies.yaml#contradiction_statuses.",
                    )
                )
            resolution = record.get("resolution")
            if status == "RESOLVED" and (not isinstance(resolution, str) or not resolution.strip()):
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "CONTRADICTION-RESOLUTION",
                        f"resolved contradiction {record_id} must include a non-empty resolution",
                        line=line,
                        field="resolution",
                        remediation="Record the resolution text or change status from RESOLVED.",
                    )
                )


def _profile_signal_paths(profiles_root: Path) -> list[Path]:
    if profiles_root.is_file():
        return [profiles_root] if profiles_root.name == "aesthetic-signals.yaml" else []
    if not profiles_root.is_dir():
        return []
    return sorted(profiles_root.rglob("aesthetic-signals.yaml"))


def _validate_profiles(
    root: Path,
    profiles_root: Path,
    validator: Draft202012Validator | None,
    vocab: dict[str, Any],
    findings: list[Finding],
) -> None:
    """Validate profile instances without materializing them in the repository."""
    paths = _profile_signal_paths(profiles_root)
    if not paths:
        return

    evidence_keys: set[str] = set()
    for project in iter_project_dirs(root):
        evidence_path = project / "02_evidence" / "evidence-ledger.jsonl"
        if not evidence_path.exists():
            continue
        try:
            records = read_jsonl_with_lines(evidence_path)
        except Exception:
            continue
        project_id = f"project/{project.name}"
        for _, record in records:
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                evidence_keys.add(f"{project_id}::{record['id']}")

    seen_ids: dict[str, Path] = {}
    relationship_strengths = set(vocab.get("aesthetic_signal_strengths", []))
    relationship_contexts = set(vocab.get("aesthetic_signal_contexts", []))
    epistemic_statuses = set(vocab.get("epistemic_statuses", []))
    qualified_evidence = re.compile(r"^project/[a-z0-9]+(?:-[a-z0-9]+)*::EV[0-9]{3,}$")

    for path in paths:
        try:
            value = load_yaml(path)
        except Exception as exc:
            findings.append(_exception_finding(root, path, "PROFILE-YAML", exc))
            continue
        if not isinstance(value, dict) or not isinstance(value.get("signals"), list):
            findings.append(
                _knowledge_finding(
                    root,
                    path,
                    "PROFILE-STRUCTURE",
                    "profile document must contain a signals list",
                    field="signals",
                    remediation="Use templates/profile/aesthetic-signals.yaml and place signal objects under signals.",
                )
            )
            continue
        for index, signal in enumerate(value["signals"]):
            field_prefix = f"signals[{index}]"
            if not isinstance(signal, dict):
                continue
            signal_id = signal.get("id")
            if isinstance(signal_id, str):
                previous = seen_ids.get(signal_id)
                if previous:
                    findings.append(
                        _knowledge_finding(
                            root,
                            path,
                            "DUPLICATE-ID",
                            f"ID {signal_id!r} is already registered in {previous}",
                            field=f"{field_prefix}.id",
                            remediation="Assign a unique AS ID across the profile root.",
                        )
                    )
                else:
                    seen_ids[signal_id] = path
            if validator:
                findings.extend(_schema_findings(root, path, validator, signal, field_prefix=field_prefix))

            strength = signal.get("strength")
            if strength not in relationship_strengths:
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "PROFILE-VOCABULARY",
                        f"aesthetic signal strength {strength!r} is not declared in config/vocabularies.yaml",
                        field=f"{field_prefix}.strength",
                        remediation="Use a value from config/vocabularies.yaml#aesthetic_signal_strengths.",
                    )
                )
            context = signal.get("context")
            if context not in relationship_contexts:
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "PROFILE-VOCABULARY",
                        f"aesthetic signal context {context!r} is not declared in config/vocabularies.yaml",
                        field=f"{field_prefix}.context",
                        remediation="Use a value from config/vocabularies.yaml#aesthetic_signal_contexts.",
                    )
                )
            confidence_status = signal.get("confidence_status")
            if confidence_status not in epistemic_statuses:
                findings.append(
                    _knowledge_finding(
                        root,
                        path,
                        "PROFILE-VOCABULARY",
                        f"confidence status {confidence_status!r} is not declared in config/vocabularies.yaml",
                        field=f"{field_prefix}.confidence_status",
                        remediation="Use a value from config/vocabularies.yaml#epistemic_statuses.",
                    )
                )

            for ref_field in ("evidence_refs", "counterexample_refs"):
                refs = signal.get(ref_field)
                if not isinstance(refs, list):
                    continue
                for ref_index, reference in enumerate(refs):
                    ref_field_path = f"{field_prefix}.{ref_field}[{ref_index}]"
                    if not isinstance(reference, str) or not qualified_evidence.fullmatch(reference):
                        findings.append(
                            _knowledge_finding(
                                root,
                                path,
                                "PROFILE-REFERENCE",
                                f"{ref_field} must contain project-qualified evidence IDs, got {reference!r}",
                                field=ref_field_path,
                                remediation="Use project/<slug>::EV### references that resolve to project evidence.",
                            )
                        )
                    elif reference not in evidence_keys:
                        findings.append(
                            _knowledge_finding(
                                root,
                                path,
                                "PROFILE-REFERENCE",
                                f"{ref_field} references missing evidence {reference!r}",
                                field=ref_field_path,
                                remediation="Create the referenced project evidence or correct the qualified ID.",
                            )
                        )

            period = signal.get("observed_period")
            review_after = signal.get("review_after")
            if isinstance(period, dict) and isinstance(review_after, str):
                from_date = period.get("from")
                to_date = period.get("to")
                try:
                    parsed_from = date.fromisoformat(from_date)
                    parsed_to = date.fromisoformat(to_date)
                    parsed_review = date.fromisoformat(review_after)
                except (TypeError, ValueError):
                    continue
                if not (parsed_from <= parsed_to <= parsed_review):
                    findings.append(
                        _knowledge_finding(
                            root,
                            path,
                            "PROFILE-PERIOD",
                            "observed_period.from must be <= observed_period.to <= review_after",
                            field=f"{field_prefix}.observed_period/review_after",
                            remediation="Use chronological ISO dates for the observation period and review date.",
                        )
                    )


def validate_repository(
    root: Path,
    project_target: str | None = None,
    profiles_root: Path | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    vocab_path = root / "config" / "vocabularies.yaml"
    access_path = root / "config" / "access-policy.yaml"
    handoff_policy_path = root / "config" / "handoff-policy.yaml"
    try:
        vocab = load_yaml(vocab_path) or {}
    except Exception as exc:
        findings.append(_exception_finding(root, vocab_path, "YAML", exc))
        vocab = {}
    try:
        access = load_yaml(access_path) or {}
    except Exception as exc:
        findings.append(_exception_finding(root, access_path, "YAML", exc))
        access = {}
    try:
        handoff_policy = load_yaml(handoff_policy_path) or {}
    except Exception as exc:
        findings.append(_exception_finding(root, handoff_policy_path, "YAML", exc))
        handoff_policy = {}

    for path in sorted((root / "config").glob("*.yaml")):
        if path in {vocab_path, access_path, handoff_policy_path}:
            continue
        try:
            load_yaml(path)
        except Exception as exc:
            findings.append(_exception_finding(root, path, "YAML", exc))

    schema_validators = _load_schema_validators(root, findings)

    forbidden = set(access.get("forbidden_extensions", [])) if isinstance(access, dict) else set()
    forbidden_filenames = access.get("forbidden_filenames", []) if isinstance(access, dict) else []
    secret_patterns = access.get("secret_patterns", []) if isinstance(access, dict) else []
    ignored_parts = {".git", ".venv", "__pycache__"}
    for path in root.rglob("*"):
        if not path.is_file() or ignored_parts.intersection(path.parts):
            continue
        if path.suffix.lower() in forbidden:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "DATA-BOUNDARY",
                    f"forbidden extension {path.suffix}",
                    remediation="Remove the raw/private artifact from the repository and retain only permitted metadata.",
                )
            )
        if any(fnmatch.fnmatch(path.name, pattern) for pattern in forbidden_filenames if isinstance(pattern, str)):
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "DATA-BOUNDARY",
                    f"forbidden filename {path.name}",
                    remediation="Remove the credential or private artifact and retain only permitted opaque metadata.",
                )
            )
        findings.extend(_secret_findings(root, path, secret_patterns))
    try:
        for security_finding in scan_advanced_security(root, access if isinstance(access, dict) else {}):
            findings.append(
                Finding(
                    security_finding.path,
                    security_finding.rule,
                    security_finding.message,
                    remediation=security_finding.remediation,
                )
            )
    except Exception as exc:
        findings.append(
            Finding(
                "config/access-policy.yaml",
                "SECURITY-CONFIG",
                str(exc),
                remediation="Fix the advanced security policy and rerun validation.",
            )
        )

    _check_execution_queue_state(root, findings)

    statuses = set(vocab.get("project_statuses", [])) if isinstance(vocab, dict) else set()
    projects = list(iter_project_dirs(root))
    project_ids = {f"project/{project.name}" for project in projects}
    selected_projects = projects
    if project_target:
        slug = project_target.split("/", 1)[1] if project_target.startswith("project/") else project_target
        selected_projects = [project for project in projects if project.name == slug]
        if not selected_projects:
            findings.append(
                Finding(
                    "projects",
                    "PROJECT-TARGET",
                    f"project target {project_target!r} was not found",
                    remediation="Use an existing project slug or project/<slug> target.",
                )
            )
    for project in selected_projects:
        relative_project = project.relative_to(root)
        entries: dict[str, RecordEntry] = {}
        extension_values: dict[str, Any] = {}
        handoff_value: Any = None
        visual_language_value: Any = None
        feedback_records: list[tuple[int, dict[str, Any]]] = []
        completion_report_value: Any = None
        run_events: list[tuple[int, dict[str, Any]]] = []
        for required in PROJECT_REQUIRED_FILES:
            if not (project / required).exists():
                findings.append(
                    Finding(
                        str(relative_project / required),
                        "PROJECT-STRUCTURE",
                        "required file missing",
                        remediation="Create the file from templates/project or restore the canonical project structure.",
                    )
                )

        manifest_path = project / "manifest.yaml"
        manifest: Any = None
        try:
            manifest = load_yaml(manifest_path) or {}
        except Exception as exc:
            findings.append(_exception_finding(root, manifest_path, "YAML", exc))
        if isinstance(manifest, dict):
            project_data = manifest.get("project", {})
            if not isinstance(project_data, dict):
                project_data = {}
            expected_id = f"project/{project.name}"
            if project_data.get("id") != expected_id:
                findings.append(
                    Finding(
                        _relative_path(root, manifest_path),
                        "PROJECT-ID",
                        f"expected {expected_id}",
                        field="project.id",
                        remediation="Set project.id to project/<directory-slug>.",
                    )
                )
            status = project_data.get("status")
            if status not in statuses:
                findings.append(
                    Finding(
                        _relative_path(root, manifest_path),
                        "PROJECT-STATUS",
                        f"invalid status {status!r}",
                        field="project.status",
                        remediation="Use a project status from config/vocabularies.yaml.",
                    )
                )
            access_data = manifest.get("access") or {}
            if not isinstance(access_data, dict):
                access_data = {}
            classification = access_data.get("classification")
            if classification in {"PRIVATE_RAW", "RESTRICTED"}:
                findings.append(
                    Finding(
                        _relative_path(root, manifest_path),
                        "DATA-BOUNDARY",
                        "project manifest cannot use a Git-prohibited classification",
                        field="access.classification",
                        remediation="Use PROJECT_INTERNAL, PUBLIC_CITABLE, or an approved PRIVATE_DERIVED boundary.",
                    )
                )
            manifest_validator = schema_validators.get("project-manifest")
            if manifest_validator:
                findings.extend(_schema_findings(root, manifest_path, manifest_validator, manifest))

        for path in sorted(project.rglob("*.yaml")):
            if path == manifest_path:
                continue
            try:
                value = load_yaml(path)
            except Exception as exc:
                findings.append(_exception_finding(root, path, "YAML", exc))
                continue
            relative = path.relative_to(project).as_posix()
            target = SCHEMA_FOR_YAML_COLLECTION.get(relative)
            if target:
                key, schema_name = target
                _validate_yaml_collection(root, path, value, key, schema_validators.get(schema_name), findings)
                if isinstance(value, dict) and isinstance(value.get(key), list):
                    _register_records(root, value[key], schema_name, path, entries, findings)
                    extension_values[schema_name] = value[key]
                    if schema_name == "production-hypothesis":
                        for hypothesis in value[key]:
                            if isinstance(hypothesis, dict) and isinstance(hypothesis.get("uncertainties"), list):
                                _register_records(root, hypothesis["uncertainties"], "uncertainty", path, entries, findings)
                    elif schema_name == "prototype-plan":
                        for plan in value[key]:
                            if isinstance(plan, dict) and isinstance(plan.get("tasks"), list):
                                _register_records(root, plan["tasks"], "prototype_task", path, entries, findings)
            elif relative in SCHEMA_FOR_YAML_OBJECT:
                schema_name = SCHEMA_FOR_YAML_OBJECT[relative]
                if value == {} and (manifest.get("workflow_mode", "RESEARCH_ONLY") if isinstance(manifest, dict) else "RESEARCH_ONLY") != HANDOFF_MODE:
                    continue
                validator = schema_validators.get(schema_name)
                if validator:
                    findings.extend(_schema_findings(root, path, validator, value))
                if relative == "01_planning/research-plan.yaml":
                    try:
                        load_completion_quality_policy(root, value)
                    except CompletionQualityConfigError as exc:
                        findings.append(
                            Finding(
                                _relative_path(root, path),
                                "COMPLETION-MINIMUM-OVERRIDE",
                                str(exc),
                                field="minimums",
                                remediation="Use canonical completion minimum keys and provide minimums.reason when lowering a default.",
                            )
                        )
                if relative == "05_production/visual-language.yaml":
                    visual_language_value = value
                if relative == HANDOFF_PATH:
                    handoff_value = value
            elif relative == "01_planning/question-register.yaml":
                if isinstance(value, dict) and isinstance(value.get("questions"), list):
                    _register_records(root, value["questions"], "question", path, entries, findings)
            elif relative == "05_production/acceptance-tests.yaml":
                if isinstance(value, dict) and isinstance(value.get("acceptance_tests"), list):
                    _register_records(root, value["acceptance_tests"], "acceptance_test", path, entries, findings)

        state_value: Any = None
        for path in sorted(project.rglob("*.json")):
            try:
                value = load_json(path)
            except Exception as exc:
                findings.append(_exception_finding(root, path, "JSON", exc))
                continue
            relative = path.relative_to(project).as_posix()
            schema_name = SCHEMA_FOR_JSON.get(relative)
            if schema_name:
                validator = schema_validators.get(schema_name)
                if validator:
                    findings.extend(_schema_findings(root, path, validator, value))
                if relative == "07_runtime/completion-report.json":
                    completion_report_value = value
            if relative == "07_runtime/research-state.json":
                state_value = value

        for path in sorted(project.rglob("*.jsonl")):
            try:
                records = read_jsonl_with_lines(path)
            except Exception as exc:
                findings.append(_exception_finding(root, path, "JSONL", exc))
                continue
            relative = path.relative_to(project).as_posix()
            schema_name = SCHEMA_FOR_JSONL.get(relative)
            validator = schema_validators.get(schema_name) if schema_name else None
            if relative == "07_runtime/run-log.jsonl":
                run_events.extend(records)
                # An event the stopping policy cannot read looks the same as no
                # event at all, so a malformed line is caught here rather than
                # leaving the policy to count silently past it.
                event_validator = schema_validators.get("run-log-event")
                if event_validator:
                    for line_number, record in records:
                        findings.extend(_schema_findings(root, path, event_validator, record, line=line_number))
            if relative == FEEDBACK_PATH:
                feedback_records.extend(records)
            for line_number, record in records:
                if validator:
                    findings.extend(_schema_findings(root, path, validator, record, line=line_number))
                if schema_name:
                    _register_records(root, [record], schema_name, path, entries, findings, line=line_number)
                if "approved-snapshots" in path.parts and record.get("sensitivity") in {"PRIVATE_RAW", "RESTRICTED"}:
                    findings.append(
                        Finding(
                            _relative_path(root, path),
                            "DATA-BOUNDARY",
                            "prohibited record in approved snapshots",
                            line=line_number,
                            field="sensitivity",
                            remediation="Remove the prohibited record from approved-snapshots and retain only allowed metadata.",
                        )
                    )

        _check_references(root, entries, project_ids, findings)
        _check_knowledge_semantics(root, entries, vocab if isinstance(vocab, dict) else {}, findings)
        _check_visual_language(
            root,
            project,
            manifest,
            entries,
            visual_language_value,
            vocab if isinstance(vocab, dict) else {},
            findings,
        )
        _check_bidirectional_decision_registries(root, entries, findings)
        _check_question_terminality(root, entries, vocab if isinstance(vocab, dict) else {}, findings,
                                    state_value.get("status") if isinstance(state_value, dict) else None)
        _check_requirement_tests(root, entries, findings)
        _check_claim_cycles(root, entries, findings)
        _check_handoff_contract(
            root,
            project,
            manifest,
            entries,
            extension_values,
            handoff_value,
            access if isinstance(access, dict) else {},
            handoff_policy if isinstance(handoff_policy, dict) else {},
            feedback_records,
            findings,
        )
        _check_lifecycle(
            root,
            manifest,
            state_value,
            completion_report_value,
            run_events,
            vocab if isinstance(vocab, dict) else {},
            project,
            findings,
        )

        if isinstance(state_value, dict) and isinstance(manifest, dict):
            manifest_project = manifest.get("project") or {}
            manifest_status = manifest_project.get("status") if isinstance(manifest_project, dict) else None
            if state_value.get("status") != manifest_status:
                state_path = project / "07_runtime" / "research-state.json"
                findings.append(
                    Finding(
                        _relative_path(root, state_path),
                        "STATE-SYNC",
                        "runtime status differs from manifest",
                        field="status",
                        remediation="Set research-state.status and manifest.project.status to the same lifecycle state.",
                    )
                )

    _validate_profiles(
        root,
        (profiles_root or (root / "profiles")).resolve(),
        schema_validators.get("aesthetic-signal"),
        vocab if isinstance(vocab, dict) else {},
        findings,
    )

    return sorted(findings, key=lambda item: (item.path, item.line or 0, item.field or "", item.rule, item.message))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate repository contracts and safety boundaries.")
    parser.add_argument("--check", action="store_true", help="Validate without generating or modifying files.")
    parser.add_argument("--project", help="Validate one project slug or project/<slug> while retaining repository-wide safety checks.")
    parser.add_argument("--profiles-root", type=Path, help="Read external profile instances from this root (defaults to <root>/profiles).")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    findings = validate_repository(args.root.resolve(), args.project, args.profiles_root.resolve() if args.profiles_root else None)
    if findings:
        for finding in findings:
            print(finding.render())
        print(f"FAILED: {len(findings)} finding(s)")
        return 1
    print("OK: repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
