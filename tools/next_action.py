#!/usr/bin/env python3
"""Hand the agent the next task, and everything it needs to finish that task.

    python3 tools/next_action.py project/<slug> --worker aiko-art --now 2026-08-21T09:00:00+09:00

Every part of the answer already existed: the runtime knows which task is
ready, the context pack knows what to read, the protocol knows how to work,
the stopping policy knows when to stop. What was missing was one place that
puts them together, so an agent with no conversation history can start.

This tool composes. It decides nothing on its own: if the answer looks
wrong, the wrong thing is in the runtime, the role table, or the protocol,
not here.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import stopping_policy
import task_runtime
from _common import ROOT, InputParseError, atomic_write_text, load_yaml, read_jsonl, stable_json
from context_pack import build_context_pack

ROLE_TABLE_PATH = "config/task-roles.yaml"
BOUNDARY_PATH = "config/execution-boundary.yaml"
PROTOCOL_PATH = "docs/research-task-protocol.md"


class NextActionError(ValueError):
    """The next action cannot be determined."""


def _project(root: Path, target: str) -> Path:
    if not target.startswith("project/") or target.count("/") != 1:
        raise NextActionError("target must be project/<slug>")
    project = root / "projects" / target.split("/", 1)[1]
    if not project.is_dir():
        raise NextActionError(f"project not found: {target}")
    return project


def resolve_role(table: dict[str, Any], task: dict[str, Any], task_id: str) -> str:
    """A task states its own role; the table is the fallback for plans written before roles existed."""
    declared = task.get("role")
    if isinstance(declared, str) and declared:
        return declared
    by_id = table.get("by_task_id") or {}
    if task_id in by_id:
        return str(by_id[task_id])
    title = str(task.get("title", ""))
    for entry in table.get("by_title_contains") or []:
        if isinstance(entry, dict) and str(entry.get("match", "")) in title:
            return str(entry["role"])
    return str(table.get("default_role", "collector"))


def protocol_sections(text: str, wanted: list[int]) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    parts = re.split(r"^## (\d+)\. (.+)$", text, flags=re.MULTILINE)
    for index in range(1, len(parts) - 2, 3):
        number = int(parts[index])
        if number in wanted:
            sections.append({"section": number, "title": parts[index + 1].strip(), "body": parts[index + 2].strip()})
    return sections


def budget_remaining(project: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """The plan states a budget nothing reads. Spending it silently is the same as not having one."""
    plan = load_yaml(project / "01_planning/research-plan.yaml") or {}
    budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
    questions = {str(event.get("question_id")) for event in events if event.get("question_id")}
    sources = {
        str(event.get("source_id") or event.get("evidence_id"))
        for event in events
        if event.get("event_type") in stopping_policy.SOURCE_EVENTS
        and (event.get("source_id") or event.get("evidence_id"))
    }
    spent = {"questions": len(questions), "total_sources": len(sources)}
    remaining: dict[str, Any] = {"spent": spent, "limits": dict(budget), "exceeded": []}
    for limit_key, spent_key in (("max_questions", "questions"), ("max_total_sources", "total_sources")):
        limit = budget.get(limit_key)
        if isinstance(limit, int):
            remaining[spent_key] = limit - spent[spent_key]
            if spent[spent_key] > limit:
                remaining["exceeded"].append(limit_key)
    return remaining


def _write_targets(role_entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(target) for target in role_entry.get("write_targets") or []]


def _acceptance(role_entry: dict[str, Any], slug: str) -> list[str]:
    return [str(command).replace("{slug}", slug) for command in role_entry.get("acceptance") or []]


def _at_end(root: Path, target: str, slug: str, project: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """No task is ready. Say which tool carries the project forward instead of stopping in silence."""
    return {
        "project_id": target,
        "status": "NO_TASK_READY",
        "next_steps": [
            f"python3 tools/stopping_policy.py project/{slug} apply --evaluated-at <RFC3339>",
            f"python3 tools/complete.py project/{slug}",
            f"python3 tools/build_handoff.py projects/{slug} --root .",
            f"python3 tools/export_handoff.py projects/{slug} --root . --output <bundle>",
        ],
        "stopping": stopping_policy.evaluate_project(root, target),
        "budget": budget_remaining(project, events),
    }


def _held_by(runtime: dict[str, Any], worker_id: str, now: str) -> dict[str, Any] | None:
    """A task this worker already holds is not a new task.

    Asking twice happens: a session dies mid-task, or the answer could not be
    built after the claim. Claiming again would take a second attempt off the
    budget and leave the first lease stranded, so the held task is returned
    as it stands.
    """
    moment = task_runtime._parse_timestamp(now)
    for task_id in sorted(runtime.get("tasks") or {}):
        task = runtime["tasks"][task_id]
        lease = task.get("lease")
        if task.get("status") != "RUNNING" or not isinstance(lease, dict):
            continue
        if lease.get("owner") != worker_id:
            continue
        if task_runtime._parse_timestamp(str(lease["expires_at"])) <= moment:
            continue
        return {"task_id": task_id, "lease": lease, "resumed": True}
    return None


def _over_budget(root: Path, target: str, slug: str, project: Path, events: list[dict[str, Any]],
                 budget: dict[str, Any]) -> dict[str, Any]:
    """The plan states a budget. Claiming another task past it spends what the project said it would not."""
    return {
        "project_id": target,
        "status": "BUDGET_EXCEEDED",
        "budget_remaining": budget,
        "stopping": stopping_policy.evaluate_project(root, target),
        "directive": (
            "予算を超えている。新しいタスクを取らない。開いている必須質問を "
            "ANSWERED か UNRESOLVED で終端させ、理由を question-register に書いてから、"
            "停止判定を適用して完了工程へ進む。"
        ),
        "next_steps": [
            f"# 01_planning/question-register.yaml の OPEN な質問を終端させる（推測で埋めない）",
            f"python3 tools/stopping_policy.py project/{slug} apply --evaluated-at <RFC3339>",
            f"python3 tools/complete.py project/{slug}",
        ],
    }


def build_next_action(root: Path, target: str, worker_id: str, now: str) -> dict[str, Any]:
    project = _project(root, target)
    slug = target.split("/", 1)[1]
    events = read_jsonl(project / "07_runtime" / "run-log.jsonl")
    budget = budget_remaining(project, events)
    claim = _held_by(task_runtime.load_runtime(root, target), worker_id, now)
    if claim is None and budget["exceeded"]:
        # 保持中のタスクは終わらせてよい。取っていないタスクを新たに取るのは止める。
        return _over_budget(root, target, slug, project, events, budget)
    if claim is None:
        fresh = task_runtime.claim_next(root, target, worker_id, now=now)
        if fresh is not None:
            # claim_next reports the lease flat; the agent needs the token to complete,
            # so it travels in one place regardless of which path produced the claim.
            claim = {
                "task_id": fresh["task_id"],
                "lease": {"owner": worker_id, "token": fresh["lease_token"], "expires_at": fresh["lease_expires_at"]},
                "resumed": False,
            }
    if claim is None:
        return _at_end(root, target, slug, project, events)

    task_id = str(claim["task_id"])
    runtime = task_runtime.load_runtime(root, target)
    task = dict(runtime["tasks"][task_id])
    # The runtime keeps only what it needs to schedule, so the role the plan
    # declared is not in it. Reading the role from the runtime silently fell
    # back to the id table and handed the agent another role's instructions.
    plan = load_yaml(project / "01_planning/research-plan.yaml") or {}
    declared = next(
        (item for item in plan.get("tasks") or [] if str(item.get("id")) == task_id),
        {},
    )
    if declared.get("role"):
        task["role"] = declared["role"]
    table = load_yaml(root / ROLE_TABLE_PATH) or {}
    role = resolve_role(table, task, task_id)
    role_entry = (table.get("roles") or {}).get(role)
    if not isinstance(role_entry, dict):
        raise NextActionError(f"role {role!r} is not described in {ROLE_TABLE_PATH}")

    boundary = load_yaml(root / BOUNDARY_PATH) or {}
    constraints = load_yaml(project / "00_intake/constraints.yaml") or {}
    protocol = (root / PROTOCOL_PATH).read_text(encoding="utf-8")

    return {
        "project_id": target,
        "status": "TASK_RESUMED" if claim.get("resumed") else "TASK_CLAIMED",
        "task_id": task_id,
        "role": role,
        "lease": claim.get("lease"),
        "context": build_context_pack(root, target, task_id, role),
        "instructions": protocol_sections(protocol, list(role_entry.get("protocol_sections") or [])),
        "write_targets": _write_targets(role_entry),
        "acceptance": _acceptance(role_entry, slug),
        "budget_remaining": budget,
        "stopping": stopping_policy.evaluate_project(root, target),
        "forbidden": {
            "operations": (boundary.get("worker_policy") or {}).get("forbidden_operations") or [],
            "project_prohibited_actions": constraints.get("prohibited_actions") or [],
        },
        "on_completion": [
            *_acceptance(role_entry, slug),
            f"python3 tools/task_runtime.py project/{slug} complete --task-id {task_id} "
            f"--worker-id {worker_id} --lease-token {claim.get('lease', {}).get('token', '<token>')} --now <RFC3339>",
            f"python3 tools/next_action.py project/{slug} --worker {worker_id} --now <RFC3339>",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Claim the next ready task and describe how to finish it.")
    parser.add_argument("target")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--now", required=True, help="RFC 3339。時刻は呼び出し側が渡す")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        content = stable_json(build_next_action(args.root.resolve(), args.target, args.worker, args.now))
    except (NextActionError, InputParseError, task_runtime.TaskRuntimeError,
            stopping_policy.StoppingPolicyError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
