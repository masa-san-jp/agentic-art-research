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
import shlex
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
    return [dict(target) for target in role_entry.get("write_targets") or [] if target.get("namespace", "worker") == "worker"]


def _runtime_targets(role_entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(target) for target in role_entry.get("runtime_targets") or []]


def _rooted_command(
    command: str,
    slug: str,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
) -> str:
    """Make a role acceptance command executable independently of cwd."""

    rendered = command.replace("{slug}", slug)
    rendered = re.sub(
        r"python3 tools/([A-Za-z0-9_.-]+)",
        lambda match: f"python3 {shlex.quote(str(protocol_root / 'tools' / match.group(1)))}",
        rendered,
    )
    project_path = work_root / "projects" / slug
    # Python -c snippets already quote their paths; replacing with an absolute
    # path inside those quotes keeps the snippet valid.  Shell commands get a
    # shell-quoted path instead.
    project_value = str(project_path) if "python3 -c" in rendered else shlex.quote(str(project_path))
    rendered = rendered.replace(f"projects/{slug}", project_value)
    rendered = rendered.replace("--root .", f"--root {shlex.quote(str(work_root))}")
    if "<bundle>" in rendered:
        rendered = rendered.replace("<bundle>", shlex.quote(str(output_root / slug)))
    script_names = (
        "validate.py",
        "stopping_policy.py",
        "build_graph.py",
        "complete.py",
        "build_handoff.py",
        "export_handoff.py",
        "task_runtime.py",
        "next_action.py",
    )
    if any(name in rendered for name in script_names) and "--root " not in rendered:
        rendered += f" --root {shlex.quote(str(work_root))}"
    return rendered


def _acceptance(
    role_entry: dict[str, Any],
    slug: str,
    *,
    protocol_root: Path | None = None,
    work_root: Path | None = None,
    output_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return only typed gates; completion is owned by the harness executor.

    The old role table exposed arbitrary shell commands.  Keeping command
    rendering here would reintroduce cwd/PATH/injection ambiguity, so the
    handoff contains the declarative gate plus all roots needed by the
    provider-neutral executor.
    """

    if "acceptance" in role_entry:
        raise NextActionError("legacy shell acceptance is forbidden")
    raw_checks = role_entry.get("acceptance_checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise NextActionError("role acceptance_checks must be a non-empty list")
    protocol = (protocol_root or work_root or ROOT).resolve()
    work = (work_root or protocol).resolve()
    output = (output_root or work / "data" / "handoffs").resolve()
    acceptance: list[dict[str, Any]] = []
    for raw in raw_checks:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not isinstance(raw.get("kind"), str):
            raise NextActionError("acceptance_checks must contain typed id and kind")
        check = dict(raw)
        if "path" in check:
            path = check["path"]
            if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
                raise NextActionError("acceptance check path must stay inside the project")
        check.update(
            {
                "project_id": f"project/{slug}",
                "protocol_root": str(protocol),
                "work_root": str(work),
                "output_root": str(output),
            }
        )
        acceptance.append(check)
    return acceptance


def _at_end(
    root: Path,
    target: str,
    slug: str,
    project: Path,
    events: list[dict[str, Any]],
    *,
    protocol_root: Path | None = None,
    work_root: Path | None = None,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """No task is ready. Say which tool carries the project forward instead of stopping in silence."""
    if protocol_root is None and work_root is None and output_root is None:
        next_steps = [
            f"python3 tools/stopping_policy.py project/{slug} apply --evaluated-at <RFC3339>",
            f"python3 tools/complete.py project/{slug}",
            f"python3 tools/build_handoff.py projects/{slug} --root .",
            f"python3 tools/export_handoff.py projects/{slug} --root . --output <bundle>",
        ]
    else:
        protocol = (protocol_root or work_root).resolve()
        work = (work_root or protocol).resolve()
        output = (output_root or work / "data" / "handoffs").resolve()
        next_steps = [
            _rooted_command(
                f"python3 tools/stopping_policy.py project/{slug} apply --evaluated-at <RFC3339>",
                slug, protocol, work, output,
            ),
            _rooted_command(f"python3 tools/complete.py project/{slug}", slug, protocol, work, output),
            _rooted_command(f"python3 tools/build_handoff.py project/{slug}", slug, protocol, work, output),
            _rooted_command(f"python3 tools/export_handoff.py project/{slug} --output <bundle>", slug, protocol, work, output),
        ]
    return {
        "project_id": target,
        "status": "NO_TASK_READY",
        "next_steps": next_steps,
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
                 budget: dict[str, Any], *, protocol_root: Path | None = None,
                 work_root: Path | None = None, output_root: Path | None = None) -> dict[str, Any]:
    """The plan states a budget. Claiming another task past it spends what the project said it would not."""
    if protocol_root is None and work_root is None and output_root is None:
        next_steps = [
            f"# 01_planning/question-register.yaml の OPEN な質問を終端させる（推測で埋めない）",
            f"python3 tools/stopping_policy.py project/{slug} apply --evaluated-at <RFC3339>",
            f"python3 tools/complete.py project/{slug}",
        ]
    else:
        protocol = (protocol_root or work_root).resolve()
        work = (work_root or protocol).resolve()
        output = (output_root or work / "data" / "handoffs").resolve()
        next_steps = [
            "# 01_planning/question-register.yaml の OPEN な質問を終端させる（推測で埋めない）",
            _rooted_command(f"python3 tools/stopping_policy.py project/{slug} apply --evaluated-at <RFC3339>", slug, protocol, work, output),
            _rooted_command(f"python3 tools/complete.py project/{slug}", slug, protocol, work, output),
        ]
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
        "next_steps": next_steps,
    }


def build_next_action(
    root: Path,
    target: str,
    worker_id: str,
    now: str,
    *,
    dry_run: bool = False,
    protocol_root: Path | None = None,
    work_root: Path | None = None,
    output_root: Path | None = None,
    memory_query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if memory_query is not None and memory_query.get("at") != now:
        raise NextActionError("memory query must use the current task clock")
    root_aware = protocol_root is not None or work_root is not None or output_root is not None
    protocol = (protocol_root or root).resolve()
    work = (work_root or root).resolve()
    output = (output_root or work / "data" / "handoffs").resolve()
    project = _project(work, target)
    slug = target.split("/", 1)[1]
    events = read_jsonl(project / "07_runtime" / "run-log.jsonl")
    budget = budget_remaining(project, events)
    preview = task_runtime.peek_next(work, target, worker_id, now=now)
    if preview is None:
        if budget["exceeded"]:
            return _over_budget(work, target, slug, project, events, budget,
                                protocol_root=protocol if root_aware else None,
                                work_root=work if root_aware else None,
                                output_root=output if root_aware else None)
        return _at_end(work, target, slug, project, events,
                       protocol_root=protocol if root_aware else None,
                       work_root=work if root_aware else None,
                       output_root=output if root_aware else None)

    # A worker may finish its already-held task even after the project budget
    # is exceeded. New work is refused at the budget boundary.
    if dry_run:
        if not preview.get("resumed") and budget["exceeded"]:
            return _over_budget(work, target, slug, project, events, budget,
                                protocol_root=protocol if root_aware else None,
                                work_root=work if root_aware else None,
                                output_root=output if root_aware else None)
        claim = {
            "task_id": preview["task_id"],
            "lease": preview.get("lease"),
            "resumed": bool(preview.get("resumed")),
        }
        preview_status = "TASK_RESUME_PREVIEW" if claim["resumed"] else "TASK_PREVIEWED"
    else:
        if preview.get("resumed"):
            claim = {
                "task_id": preview["task_id"],
                "lease": preview.get("lease"),
                "resumed": True,
            }
        elif budget["exceeded"]:
            return _over_budget(
                work,
                target,
                slug,
                project,
                events,
                budget,
                protocol_root=protocol if root_aware else None,
                work_root=work if root_aware else None,
                output_root=output if root_aware else None,
            )
        else:
            fresh = task_runtime.claim_next(work, target, worker_id, now=now)
            if fresh is None:
                # Another worker may have claimed the preview between the
                # read-only peek and the live claim. Recompute the handoff.
                return _at_end(work, target, slug, project, events,
                               protocol_root=protocol if root_aware else None,
                               work_root=work if root_aware else None,
                               output_root=output if root_aware else None)
            claim = {
                "task_id": fresh["task_id"],
                "lease": {"owner": worker_id, "token": fresh["lease_token"], "expires_at": fresh["lease_expires_at"]},
                "resumed": False,
            }
        preview_status = "TASK_RESUMED" if claim["resumed"] else "TASK_CLAIMED"

    task_id = str(claim["task_id"])
    runtime = task_runtime.load_runtime(work, target)
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
    table = load_yaml(protocol / ROLE_TABLE_PATH) or {}
    role = resolve_role(table, task, task_id)
    role_entry = (table.get("roles") or {}).get(role)
    if not isinstance(role_entry, dict):
        raise NextActionError(f"role {role!r} is not described in {ROLE_TABLE_PATH}")

    boundary = load_yaml(protocol / BOUNDARY_PATH) or {}
    constraints = load_yaml(project / "00_intake/constraints.yaml") or {}
    protocol_text = (protocol / PROTOCOL_PATH).read_text(encoding="utf-8")
    lease = claim.get("lease") or {}
    lease_token = lease.get("token", "<token>")
    stored_decision = task.get("human_decision_response")
    human_decision = None
    if isinstance(stored_decision, dict):
        human_decision = {
            "request_id": stored_decision.get("request_id"),
            "request_sha256": stored_decision.get("request_sha256"),
            "response_id": stored_decision.get("id"),
            "response_sha256": stored_decision.get("response_sha256"),
            "action": stored_decision.get("action"),
            "selected_option": stored_decision.get("selected_option"),
            "resolved_at": stored_decision.get("resolved_at"),
        }

    result = {
        "project_id": target,
        "status": preview_status,
        "task_id": task_id,
        "role": role,
        "lease": claim.get("lease"),
        "context": build_context_pack(
            work,
            target,
            task_id,
            role,
            protocol_root=protocol,
            work_root=work,
            human_decision=human_decision,
            memory_query=memory_query,
        ),
        "instructions": protocol_sections(protocol_text, list(role_entry.get("protocol_sections") or [])),
        "write_targets": _write_targets(role_entry),
        "runtime_targets": _runtime_targets(role_entry),
        "acceptance": _acceptance(
            role_entry,
            slug,
            protocol_root=protocol,
            work_root=work,
            output_root=output,
        ),
        "budget_remaining": budget,
        "stopping": stopping_policy.evaluate_project(work, target),
        "forbidden": {
            "operations": (boundary.get("worker_policy") or {}).get("forbidden_operations") or [],
            "project_prohibited_actions": constraints.get("prohibited_actions") or [],
        },
        "on_completion": {
            "executor": "tools.acceptance_executor.complete_attempt",
            "acceptance": _acceptance(
                role_entry,
                slug,
                protocol_root=protocol,
                work_root=work,
                output_root=output,
            ),
            "task_runtime_owned": True,
            "next_action": {
                "tool": "tools.next_action.build_next_action",
                "project_id": target,
                "worker_id": worker_id,
                "now": "<RFC3339>",
            },
        },
    }
    if root_aware:
        result["roots"] = {
            "protocol_root": str(protocol),
            "work_root": str(work),
            "output_root": str(output),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or claim the next ready task and describe how to finish it.")
    parser.add_argument("target")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--now", required=True, help="RFC 3339。時刻は呼び出し側が渡す")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changing project state or run-log")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT, help="compatibility alias for --work-root")
    parser.add_argument("--work-root", type=Path, help="project work root")
    parser.add_argument("--protocol-root", type=Path, help="read-only protocol root")
    parser.add_argument("--output-root", type=Path, help="external successful-output root")
    parser.add_argument("--memory-query", type=Path, help="explicit external owner/creator/pinned knowledge query JSON")
    args = parser.parse_args()
    try:
        content = stable_json(
            build_next_action(
                args.root.resolve(),
                args.target,
                args.worker,
                args.now,
                dry_run=args.dry_run,
                protocol_root=args.protocol_root.resolve() if args.protocol_root else None,
                work_root=args.work_root.resolve() if args.work_root else None,
                output_root=args.output_root.resolve() if args.output_root else None,
                memory_query=__import__("json").loads(args.memory_query.read_text()) if args.memory_query else None,
            )
        )
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
