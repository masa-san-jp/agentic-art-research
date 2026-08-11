from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import ROOT, InputParseError, atomic_write_text, load_yaml, stable_json


CONTEXT_PACK_SCHEMA = "urn:agentic-art-research:context-pack:v1"
CONSTRAINTS_PATH = "00_intake/constraints.yaml"
ACCEPTANCE_TESTS_PATH = "05_production/acceptance-tests.yaml"

ROLE_SOURCE_PATHS: dict[str, tuple[str, ...]] = {
    "orchestrator": ("manifest.yaml", "07_runtime/research-state.json", "07_runtime/completion-report.json"),
    "planner": ("00_intake/creative-intent.md", "01_planning/question-register.yaml"),
    "collector": ("01_planning/question-register.yaml", "02_evidence/source-ledger.jsonl", "02_evidence/evidence-ledger.jsonl"),
    "curator": ("02_evidence/source-ledger.jsonl", "02_evidence/evidence-ledger.jsonl", "06_governance/rights-register.yaml"),
    "analyst": ("02_evidence/evidence-ledger.jsonl", "03_knowledge/observations.jsonl", "03_knowledge/claims.jsonl", "03_knowledge/relationships.jsonl", "03_knowledge/contradictions.jsonl"),
    "critic": ("03_knowledge/claims.jsonl", "03_knowledge/contradictions.jsonl", "04_decisions/insight-register.yaml", "04_decisions/uncertainty-register.yaml"),
    "production-translator": ("04_decisions/decision-log.yaml", "05_production/creative-direction.md", "05_production/production-requirements.yaml"),
    "validator": ("06_governance/rights-register.yaml", "06_governance/privacy-review.yaml", "06_governance/safety-risk-register.yaml", "07_runtime/research-state.json"),
    "auditor": ("02_evidence/evidence-ledger.jsonl", "03_knowledge/claims.jsonl", "04_decisions/decision-log.yaml", "06_governance/rights-register.yaml", "06_governance/privacy-review.yaml", "07_runtime/completion-report.json"),
}


def _project_path(root: Path, target: str) -> Path:
    if not target.startswith("project/") or target.count("/") != 1:
        raise ValueError("target must be project/<slug>")
    slug = target.split("/", 1)[1]
    if not slug:
        raise ValueError("target must be project/<slug>")
    projects_root = (root / "projects").resolve()
    project = (projects_root / slug).resolve()
    if project.parent != projects_root:
        raise ValueError("target must identify a direct project directory")
    if not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def _read_source(project: Path, relative: str) -> dict[str, str]:
    path = (project / relative).resolve()
    if project not in path.parents:
        raise ValueError(f"context source escapes project: {relative}")
    if not path.is_file():
        raise FileNotFoundError(f"context source not found: {relative}")
    return {"path": relative, "content": path.read_text(encoding="utf-8")}


def _load_task(project: Path, task_id: str) -> dict[str, Any]:
    path = project / "01_planning" / "research-plan.yaml"
    try:
        plan = load_yaml(path) or {}
    except InputParseError:
        raise
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
        raise ValueError(f"{path}: tasks must be a list")
    for task in plan["tasks"]:
        if isinstance(task, dict) and task.get("id") == task_id:
            dependencies = task.get("depends_on", [])
            if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
                raise ValueError(f"{path}: {task_id}.depends_on must be a list of strings")
            return task
    raise ValueError(f"task not found in research plan: {task_id}")


def build_context_pack(root: Path, target: str, task_id: str, role: str) -> dict[str, Any]:
    if role not in ROLE_SOURCE_PATHS:
        raise ValueError(f"unknown role: {role}")
    project = _project_path(root, target)
    task = _load_task(project, task_id)
    return {
        "schema": CONTEXT_PACK_SCHEMA,
        "project_id": target,
        "task_id": task_id,
        "role": role,
        "task": task,
        "constraints": _read_source(project, CONSTRAINTS_PATH),
        "acceptance_tests": _read_source(project, ACCEPTANCE_TESTS_PATH),
        "evidence": [_read_source(project, relative) for relative in ROLE_SOURCE_PATHS[role]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a task-minimal role context pack.")
    parser.add_argument("target")
    parser.add_argument("task_id")
    parser.add_argument("--role", choices=sorted(ROLE_SOURCE_PATHS), required=True)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        content = stable_json(build_context_pack(args.root.resolve(), args.target, args.task_id, args.role))
    except (FileNotFoundError, InputParseError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
