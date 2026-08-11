from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json, yaml_list


class ContextPackError(ValueError):
    """Raised when a role context would be incomplete or over-broad."""


COLLECTIONS = {
    "questions": ("01_planning/question-register.yaml", "questions", "yaml"),
    "evidence": ("02_evidence/evidence-ledger.jsonl", None, "jsonl"),
    "sources": ("02_evidence/source-ledger.jsonl", None, "jsonl"),
    "observations": ("03_knowledge/observations.jsonl", None, "jsonl"),
    "claims": ("03_knowledge/claims.jsonl", None, "jsonl"),
    "relationships": ("03_knowledge/relationships.jsonl", None, "jsonl"),
    "contradictions": ("03_knowledge/contradictions.jsonl", None, "jsonl"),
    "external_references": ("03_knowledge/external-references.jsonl", None, "jsonl"),
    "insights": ("04_decisions/insight-register.yaml", "insights", "yaml"),
    "decisions": ("04_decisions/decision-log.yaml", "decisions", "yaml"),
    "uncertainty": ("04_decisions/uncertainty-register.yaml", None, "yaml"),
    "requirements": ("05_production/production-requirements.yaml", "requirements", "yaml"),
    "acceptance_tests": ("05_production/acceptance-tests.yaml", "acceptance_tests", "yaml"),
    "prototype_backlog": ("05_production/prototype-backlog.yaml", None, "yaml"),
    "rights": ("06_governance/rights-register.yaml", None, "yaml"),
    "privacy": ("06_governance/privacy-review.yaml", None, "yaml"),
}
DOCUMENTS = {
    "intent": "00_intake/creative-intent.md",
    "constraints": "00_intake/constraints.yaml",
    "manifest": "manifest.yaml",
    "state": "07_runtime/research-state.json",
    "completion": "07_runtime/completion-report.json",
}
REFERENCE_FIELDS = {
    "evidence": {"question_ids": "related_questions"},
    "claims": {"evidence_ids": "evidence_ids", "question_ids": "related_questions"},
    "insights": {"claim_ids": "claim_ids"},
    "decisions": {"insight_ids": "insight_ids", "evidence_ids": "evidence_ids"},
    "requirements": {"decision_ids": "source_decisions"},
    "acceptance_tests": {"requirement_ids": "target_requirement"},
}
TASK_REFERENCE_FIELDS = {
    "question_ids": "questions",
    "evidence_ids": "evidence",
    "claim_ids": "claims",
    "insight_ids": "insights",
    "decision_ids": "decisions",
    "requirement_ids": "requirements",
    "acceptance_test_ids": "acceptance_tests",
}


def _project(root: Path, target: str) -> Path:
    if not isinstance(target, str) or not target.startswith("project/") or target.count("/") != 1:
        raise ContextPackError("target must be project/<slug>")
    projects_root = (root / "projects").resolve()
    project = (projects_root / target.split("/", 1)[1]).resolve()
    if project.parent != projects_root:
        raise ContextPackError("target must identify a direct project directory")
    if not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def _role_catalog(root: Path) -> dict[str, dict[str, Any]]:
    document = load_yaml(root / "config" / "role-context.yaml") or {}
    roles = document.get("roles") if isinstance(document, dict) else None
    if not isinstance(roles, dict) or not roles:
        raise ContextPackError("config/role-context.yaml: roles must be a non-empty mapping")
    result: dict[str, dict[str, Any]] = {}
    for role, definition in roles.items():
        if not isinstance(role, str) or not isinstance(definition, dict):
            raise ContextPackError("config/role-context.yaml: each role must have an object definition")
        allowed = definition.get("allowed_kinds")
        paths = definition.get("file_paths")
        if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
            raise ContextPackError(f"role {role}: allowed_kinds must be a list of strings")
        if not isinstance(paths, list) or any(not isinstance(value, str) for value in paths):
            raise ContextPackError(f"role {role}: file_paths must be a list of strings")
        result[role] = definition
    return result


def _load_collection(project: Path, kind: str) -> list[dict[str, Any]]:
    if kind not in COLLECTIONS:
        raise ContextPackError(f"unknown context collection: {kind}")
    relative, key, format_name = COLLECTIONS[kind]
    path = project / relative
    if format_name == "jsonl":
        records = read_jsonl(path)
    else:
        value = load_yaml(path) or {}
        if key is None:
            if not isinstance(value, dict):
                raise ContextPackError(f"{path}: expected an object")
            records = [{"value": value}]
        else:
            records = value.get(key, []) if isinstance(value, dict) else None
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ContextPackError(f"{path}: expected a list of objects")
    return records


def _load_task(project: Path, task_id: str) -> dict[str, Any]:
    plan_path = project / "01_planning/research-plan.yaml"
    plan = load_yaml(plan_path) or {}
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list):
        raise ContextPackError(f"{plan_path}: tasks must be a list for context packs")
    matches = [task for task in tasks if isinstance(task, dict) and task.get("id") == task_id]
    if not matches:
        raise FileNotFoundError(f"task not found in research plan: {task_id}")
    if len(matches) != 1:
        raise ContextPackError(f"duplicate task ID in research plan: {task_id}")
    task = matches[0]
    role = task.get("role")
    if not isinstance(role, str) or not role:
        raise ContextPackError(f"task {task_id}: role is required for a context pack")
    return task


def _id_list(task: dict[str, Any], field: str) -> set[str]:
    values = task.get(field, [])
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise ContextPackError(f"task {task.get('id')}: {field} must be a list of non-empty strings")
    if len(set(values)) != len(values):
        raise ContextPackError(f"task {task.get('id')}: {field} contains duplicates")
    return set(values)


def _selected_records(
    project: Path, task: dict[str, Any], allowed_kinds: set[str]
) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, set[str]] = {kind: set() for kind in COLLECTIONS}
    for task_field, kind in TASK_REFERENCE_FIELDS.items():
        values = _id_list(task, task_field)
        if values and kind not in allowed_kinds:
            raise ContextPackError(f"task {task['id']}: role {task['role']} cannot receive {kind}")
        selected[kind].update(values)

    records = {kind: _load_collection(project, kind) for kind in COLLECTIONS}
    by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for kind, items in records.items():
        by_id[kind] = {}
        for record in items:
            record_id = record.get("id")
            if isinstance(record_id, str):
                if record_id in by_id[kind]:
                    raise ContextPackError(f"duplicate {kind} ID: {record_id}")
                by_id[kind][record_id] = record
        missing = sorted(selected[kind] - by_id[kind].keys())
        if missing:
            raise ContextPackError(f"task {task['id']}: unknown {kind} ID {missing[0]}")

    def may_derive(kind: str) -> bool:
        return kind in allowed_kinds

    if may_derive("evidence") and selected["questions"]:
        selected["evidence"].update(
            record["id"]
            for record in records["evidence"]
            if set(record.get("related_questions", [])) & selected["questions"] and isinstance(record.get("id"), str)
        )
    if may_derive("claims") and selected["evidence"]:
        selected["claims"].update(
            record["id"]
            for record in records["claims"]
            if set(record.get("evidence_ids", [])) & selected["evidence"] and isinstance(record.get("id"), str)
        )
    if may_derive("insights") and selected["claims"]:
        selected["insights"].update(
            record["id"]
            for record in records["insights"]
            if set(record.get("claim_ids", [])) & selected["claims"] and isinstance(record.get("id"), str)
        )
    if may_derive("decisions") and (selected["insights"] or selected["evidence"]):
        selected["decisions"].update(
            record["id"]
            for record in records["decisions"]
            if (
                set(record.get("insight_ids", [])) & selected["insights"]
                or set(record.get("evidence_ids", [])) & selected["evidence"]
            )
            and isinstance(record.get("id"), str)
        )
    if may_derive("requirements") and selected["decisions"]:
        selected["requirements"].update(
            record["id"]
            for record in records["requirements"]
            if set(record.get("source_decisions", [])) & selected["decisions"] and isinstance(record.get("id"), str)
        )
    if may_derive("acceptance_tests") and selected["requirements"]:
        selected["acceptance_tests"].update(
            record["id"]
            for record in records["acceptance_tests"]
            if record.get("target_requirement") in selected["requirements"] and isinstance(record.get("id"), str)
        )

    output: dict[str, list[dict[str, Any]]] = {}
    for kind in COLLECTIONS:
        if kind not in allowed_kinds:
            if selected[kind]:
                raise ContextPackError(f"task {task['id']}: role {task['role']} cannot receive {kind}")
            continue
        output[kind] = [by_id[kind][record_id] for record_id in sorted(selected[kind])]
        for record in output[kind]:
            if record.get("sensitivity") in {"PRIVATE_RAW", "RESTRICTED"}:
                raise ContextPackError(
                    f"task {task['id']}: cannot include {record.get('sensitivity')} record {record.get('id')} in context pack"
                )
    return output


def _load_documents(project: Path, allowed_kinds: set[str]) -> dict[str, Any]:
    documents: dict[str, Any] = {}
    if "intent" in allowed_kinds:
        documents["intent"] = (project / DOCUMENTS["intent"]).read_text(encoding="utf-8")
    if "constraints" in allowed_kinds:
        documents["constraints"] = load_yaml(project / DOCUMENTS["constraints"])
    if "manifest" in allowed_kinds:
        documents["manifest"] = load_yaml(project / DOCUMENTS["manifest"])
    if "state" in allowed_kinds:
        documents["state"] = load_json(project / DOCUMENTS["state"])
    if "completion" in allowed_kinds:
        documents["completion"] = load_json(project / DOCUMENTS["completion"])
    return documents


def build_context_pack(root: Path, target: str, task_id: str, *, role: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    project = _project(root, target)
    task = _load_task(project, task_id)
    catalog = _role_catalog(root)
    task_role = task["role"]
    if task_role not in catalog:
        raise ContextPackError(f"unknown role: {task_role}")
    if role is not None and role != task_role:
        raise ContextPackError(f"task {task_id} is assigned to role {task_role}, not {role}")
    definition = catalog[task_role]
    allowed_kinds = set(definition["allowed_kinds"])
    records = _selected_records(project, task, allowed_kinds)
    task_fields = (
        "id",
        "title",
        "role",
        "depends_on",
        "question_ids",
        "evidence_ids",
        "claim_ids",
        "insight_ids",
        "decision_ids",
        "requirement_ids",
        "acceptance_test_ids",
    )
    task_context = {field: task[field] for field in task_fields if field in task}
    return {
        "version": 1,
        "project_id": f"project/{project.name}",
        "task": task_context,
        "role": {
            "id": task_role,
            "description": definition["description"],
            "prohibitions": list(definition["prohibitions"]),
        },
        "source_paths": sorted(definition["file_paths"]),
        "documents": _load_documents(project, allowed_kinds),
        "records": records,
    }


def render_context_pack(root: Path, target: str, task_id: str, *, role: str | None = None) -> str:
    return stable_json(build_context_pack(root, target, task_id, role=role))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a task-minimal role context pack.")
    parser.add_argument("target")
    parser.add_argument("task_id")
    parser.add_argument("--role")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        content = render_context_pack(args.root.resolve(), args.target, args.task_id, role=args.role)
    except (ContextPackError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
