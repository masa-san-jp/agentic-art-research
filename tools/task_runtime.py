from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json


TASK_ID = re.compile(r"^[A-Z][A-Z0-9_-]{2,}$")
TASK_RUNTIME_KEY = "task_runtime"


class TaskRuntimeError(ValueError):
    """Raised when a task runtime operation violates its persisted contract."""


def _project(root: Path, target: str) -> Path:
    if not isinstance(target, str) or not target.startswith("project/") or target.count("/") != 1:
        raise TaskRuntimeError("target must be project/<slug>")
    project = (root / "projects" / target.split("/", 1)[1]).resolve()
    projects_root = (root / "projects").resolve()
    if projects_root not in project.parents or not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise TaskRuntimeError("timestamp must be an RFC 3339 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TaskRuntimeError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TaskRuntimeError(f"timestamp must include a timezone: {value}")
    return parsed


def _timestamp(value: str | None) -> str:
    if value is not None:
        _parse_timestamp(value)
        return value
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _runtime_policy(root: Path) -> tuple[dict[str, Any], set[str], set[str], set[str]]:
    policy = load_yaml(root / "config" / "stopping-policy.yaml") or {}
    vocabulary = load_yaml(root / "config" / "vocabularies.yaml") or {}
    configured = None
    if isinstance(policy, dict):
        configured = policy.get("task_runtime")
        if configured is None and isinstance(policy.get("defaults"), dict):
            configured = policy["defaults"].get("task_runtime")
    if not isinstance(configured, dict):
        raise TaskRuntimeError("config/stopping-policy.yaml: task_runtime must be a mapping")
    task_statuses = set(vocabulary.get("task_statuses", [])) if isinstance(vocabulary, dict) else set()
    failure_classes = set(vocabulary.get("task_failure_classes", [])) if isinstance(vocabulary, dict) else set()
    retryable = set(configured.get("retryable_failure_classes", []))
    terminal = set(configured.get("terminal_failure_classes", []))
    if task_statuses != {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED"}:
        raise TaskRuntimeError("config/vocabularies.yaml: task_statuses must declare the runtime statuses")
    if not retryable or not terminal or not retryable.isdisjoint(terminal):
        raise TaskRuntimeError("config/stopping-policy.yaml: task failure classes must be disjoint")
    if not retryable | terminal <= failure_classes:
        raise TaskRuntimeError("config/vocabularies.yaml: task failure classes are incomplete")
    for key in ("default_lease_seconds", "default_max_attempts"):
        if not isinstance(configured.get(key), int) or configured[key] <= 0:
            raise TaskRuntimeError(f"config/stopping-policy.yaml: {key} must be a positive integer")
    return configured, task_statuses, failure_classes, retryable | terminal


def _canonical_tasks(root: Path, definitions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    policy, _, _, _ = _runtime_policy(root)
    default_attempts = policy["default_max_attempts"]
    raw = list(definitions)
    tasks: dict[str, dict[str, Any]] = {}
    for index, definition in enumerate(raw):
        if not isinstance(definition, dict):
            raise TaskRuntimeError(f"task definition {index} must be an object")
        task_id = definition.get("id")
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise TaskRuntimeError(f"task definition {index} has invalid id: {task_id!r}")
        if task_id in tasks:
            raise TaskRuntimeError(f"duplicate task ID: {task_id}")
        depends_on = definition.get("depends_on", [])
        if not isinstance(depends_on, list) or any(not isinstance(item, str) for item in depends_on):
            raise TaskRuntimeError(f"task {task_id}: depends_on must be a list of task IDs")
        if len(set(depends_on)) != len(depends_on):
            raise TaskRuntimeError(f"task {task_id}: depends_on contains duplicates")
        max_attempts = definition.get("max_attempts", default_attempts)
        if not isinstance(max_attempts, int) or max_attempts <= 0:
            raise TaskRuntimeError(f"task {task_id}: max_attempts must be a positive integer")
        title = definition.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise TaskRuntimeError(f"task {task_id}: title must be a non-empty string when provided")
        task: dict[str, Any] = {
            "depends_on": sorted(depends_on),
            "max_attempts": max_attempts,
            "status": "PENDING",
            "attempts": 0,
            "lease": None,
            "result": None,
            "failure": None,
            "effect_key": None,
        }
        if title is not None:
            task["title"] = title
        tasks[task_id] = task
    for task_id, task in tasks.items():
        missing = sorted(set(task["depends_on"]) - tasks.keys())
        if missing:
            raise TaskRuntimeError(f"task {task_id}: unknown dependency {missing[0]}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise TaskRuntimeError(f"task dependency cycle includes {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in sorted(tasks):
        visit(task_id)
    return {task_id: tasks[task_id] for task_id in sorted(tasks)}


def _plan_tasks(project: Path) -> list[dict[str, Any]]:
    plan_path = project / "01_planning" / "research-plan.yaml"
    plan = load_yaml(plan_path) or {}
    definitions = plan.get("tasks", []) if isinstance(plan, dict) else []
    if not isinstance(definitions, list):
        raise TaskRuntimeError(f"{plan_path}: tasks must be a list")
    return definitions


def _read_runtime(project: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state_path = project / "07_runtime" / "research-state.json"
    log_path = project / "07_runtime" / "run-log.jsonl"
    state = load_json(state_path)
    if not isinstance(state, dict):
        raise TaskRuntimeError(f"{state_path}: research state must be an object")
    runtime = state.get(TASK_RUNTIME_KEY)
    if not isinstance(runtime, dict) or not isinstance(runtime.get("tasks"), dict):
        raise TaskRuntimeError("task runtime is not initialized; call initialize_runtime first")
    events = read_jsonl(log_path)
    seen: set[str] = set()
    for index, event in enumerate(events, 1):
        event_id = event.get("event_id", event.get("id"))
        if not isinstance(event_id, str) or not event_id:
            raise TaskRuntimeError(f"run-log event {index} has no event_id")
        if event_id in seen:
            raise TaskRuntimeError(f"duplicate run-log event ID: {event_id}")
        seen.add(event_id)
    return state, events


def _next_event_id(events: list[dict[str, Any]]) -> str:
    used = {event.get("event_id", event.get("id")) for event in events}
    number = 1
    while f"TASK-EVT-{number:06d}" in used:
        number += 1
    return f"TASK-EVT-{number:06d}"


def _event(events: list[dict[str, Any]], event_type: str, occurred_at: str, **fields: Any) -> dict[str, Any]:
    return {"event_id": _next_event_id(events), "event_type": event_type, "occurred_at": occurred_at, **fields}


def _task(runtime: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = runtime.get("tasks")
    if not isinstance(tasks, dict) or task_id not in tasks:
        raise TaskRuntimeError(f"unknown task ID: {task_id}")
    record = tasks[task_id]
    if not isinstance(record, dict):
        raise TaskRuntimeError(f"task {task_id} state is not an object")
    return record


def _ready(runtime: dict[str, Any], task_id: str) -> bool:
    task = _task(runtime, task_id)
    return task.get("status") == "PENDING" and all(
        _task(runtime, dependency).get("status") == "SUCCEEDED" for dependency in task.get("depends_on", [])
    )


def _reconcile(runtime: dict[str, Any], events: list[dict[str, Any]], now: str) -> tuple[list[str], list[str]]:
    now_dt = _parse_timestamp(now)
    recovered: list[str] = []
    blocked: list[str] = []
    for task_id in sorted(runtime["tasks"]):
        task = _task(runtime, task_id)
        lease = task.get("lease")
        if task.get("status") != "RUNNING" or not isinstance(lease, dict):
            continue
        expires_at = lease.get("expires_at")
        if not isinstance(expires_at, str) or _parse_timestamp(expires_at) > now_dt:
            continue
        attempts = task["attempts"]
        task["lease"] = None
        task["failure"] = {
            "class": "LEASE_EXPIRED",
            "message": "The worker lease expired before the task completed.",
            "attempt": attempts,
            "occurred_at": now,
        }
        if attempts < task["max_attempts"]:
            task["status"] = "PENDING"
            next_status = "PENDING"
            recovered.append(task_id)
        else:
            task["status"] = "FAILED"
            next_status = "FAILED"
        events.append(
            _event(
                events,
                "TASK_LEASE_EXPIRED",
                now,
                task_id=task_id,
                attempt=attempts,
                retry_status=next_status,
                lease_token=lease.get("token"),
            )
        )

    changed = True
    while changed:
        changed = False
        for task_id in sorted(runtime["tasks"]):
            task = _task(runtime, task_id)
            if task.get("status") != "PENDING":
                continue
            dependencies = task.get("depends_on", [])
            if not any(_task(runtime, dependency).get("status") in {"FAILED", "BLOCKED"} for dependency in dependencies):
                continue
            task["status"] = "BLOCKED"
            task["failure"] = {
                "class": "DEPENDENCY_FAILED",
                "message": "A required dependency failed or was blocked.",
                "occurred_at": now,
            }
            blocked.append(task_id)
            changed = True
            events.append(_event(events, "TASK_BLOCKED", now, task_id=task_id, failure_class="DEPENDENCY_FAILED"))
    return recovered, blocked


def _reconcile_new_events(
    runtime: dict[str, Any], events: list[dict[str, Any]], generated: list[dict[str, Any]], now: str
) -> tuple[list[str], list[str]]:
    working = events + generated
    result = _reconcile(runtime, working, now)
    generated[:] = working[len(events) :]
    return result


def _mutate(
    project: Path,
    mutator: Callable[[dict[str, Any], list[dict[str, Any]]], tuple[dict[str, Any], list[dict[str, Any]], Any]],
) -> Any:
    state_path = project / "07_runtime" / "research-state.json"
    log_path = project / "07_runtime" / "run-log.jsonl"
    lock_name = "agentic-art-runtime-" + hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:24] + ".lock"
    lock_path = Path(tempfile.gettempdir()) / lock_name
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        before_state = state_path.read_text(encoding="utf-8")
        before_log = log_path.read_text(encoding="utf-8")
        state, events = _read_runtime(project)
        try:
            new_state, new_events, result = mutator(state, events)
            all_events = events + new_events
            atomic_write_text(state_path, stable_json(new_state))
            if new_events:
                atomic_write_text(log_path, "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in all_events))
            return result
        except Exception:
            atomic_write_text(state_path, before_state)
            atomic_write_text(log_path, before_log)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def initialize_runtime(
    root: Path,
    target: str,
    definitions: Iterable[dict[str, Any]] | None = None,
    *,
    initialized_at: str | None = None,
) -> dict[str, Any]:
    project = _project(root.resolve(), target)
    source = list(definitions) if definitions is not None else _plan_tasks(project)
    canonical = _canonical_tasks(root.resolve(), source)
    timestamp = _timestamp(initialized_at)
    state_path = project / "07_runtime" / "research-state.json"
    log_path = project / "07_runtime" / "run-log.jsonl"
    lock_name = "agentic-art-runtime-" + hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:24] + ".lock"
    lock_path = Path(tempfile.gettempdir()) / lock_name
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        before_state = state_path.read_text(encoding="utf-8")
        before_log = log_path.read_text(encoding="utf-8")
        try:
            state = load_json(state_path)
            events = read_jsonl(log_path)
            existing = state.get(TASK_RUNTIME_KEY) if isinstance(state, dict) else None
            if existing is not None:
                if not isinstance(existing, dict) or not isinstance(existing.get("tasks"), dict):
                    raise TaskRuntimeError("research-state.task_runtime must be an object with tasks")
                if existing.get("tasks") != canonical:
                    raise TaskRuntimeError("task runtime is already initialized with different task definitions")
                return existing
            runtime = {"version": 1, "initialized_at": timestamp, "tasks": canonical}
            state[TASK_RUNTIME_KEY] = runtime
            state["current_task"] = None
            state["resume_from"] = "Claim the lowest-ID ready task from 07_runtime task_runtime."
            event = _event(events, "TASK_RUNTIME_INITIALIZED", timestamp, task_ids=sorted(canonical))
            atomic_write_text(state_path, stable_json(state))
            atomic_write_text(log_path, before_log + json.dumps(event, ensure_ascii=False) + "\n")
            return runtime
        except Exception:
            atomic_write_text(state_path, before_state)
            atomic_write_text(log_path, before_log)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def load_runtime(root: Path, target: str) -> dict[str, Any]:
    project = _project(root.resolve(), target)
    state, _ = _read_runtime(project)
    return state[TASK_RUNTIME_KEY]


def claim_next(
    root: Path,
    target: str,
    worker_id: str,
    *,
    now: str | None = None,
    lease_seconds: int | None = None,
) -> dict[str, Any] | None:
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise TaskRuntimeError("worker_id must be a non-empty string")
    root = root.resolve()
    policy, _, _, _ = _runtime_policy(root)
    seconds = policy["default_lease_seconds"] if lease_seconds is None else lease_seconds
    if not isinstance(seconds, int) or seconds <= 0:
        raise TaskRuntimeError("lease_seconds must be a positive integer")
    occurred_at = _timestamp(now)

    def mutate(state: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
        runtime = state[TASK_RUNTIME_KEY]
        generated: list[dict[str, Any]] = []
        _reconcile_new_events(runtime, events, generated, occurred_at)
        for task_id in sorted(runtime["tasks"]):
            if not _ready(runtime, task_id):
                continue
            task = _task(runtime, task_id)
            task["attempts"] += 1
            expires_at = (_parse_timestamp(occurred_at) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
            token = f"{task_id}:attempt:{task['attempts']}"
            task["status"] = "RUNNING"
            task["lease"] = {"owner": worker_id, "token": token, "expires_at": expires_at}
            task["failure"] = None
            state["current_task"] = task_id
            generated.append(
                _event(
                    events + generated,
                    "TASK_CLAIMED",
                    occurred_at,
                    task_id=task_id,
                    attempt=task["attempts"],
                    worker_id=worker_id,
                    lease_token=token,
                    lease_expires_at=expires_at,
                    effect_key=task_id,
                )
            )
            return state, generated, {
                "task_id": task_id,
                "attempt": task["attempts"],
                "lease_token": token,
                "lease_expires_at": expires_at,
                "effect_key": task_id,
            }
        return state, generated, None

    return _mutate(_project(root, target), mutate)


def resume(root: Path, target: str, *, now: str | None = None) -> dict[str, Any]:
    occurred_at = _timestamp(now)

    def mutate(state: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
        runtime = state[TASK_RUNTIME_KEY]
        generated: list[dict[str, Any]] = []
        recovered, blocked = _reconcile_new_events(runtime, events, generated, occurred_at)
        ready = [task_id for task_id in sorted(runtime["tasks"]) if _ready(runtime, task_id)]
        if state.get("current_task") not in {task_id for task_id, record in runtime["tasks"].items() if record.get("status") == "RUNNING"}:
            state["current_task"] = None
        return state, generated, {"recovered": recovered, "blocked": blocked, "ready": ready}

    return _mutate(_project(root.resolve(), target), mutate)


def _assert_lease(task: dict[str, Any], worker_id: str, lease_token: str) -> None:
    lease = task.get("lease")
    if task.get("status") != "RUNNING" or not isinstance(lease, dict):
        raise TaskRuntimeError(f"task is not running: {task}")
    if lease.get("owner") != worker_id or lease.get("token") != lease_token:
        raise TaskRuntimeError("worker does not hold the current task lease")


def complete(
    root: Path,
    target: str,
    task_id: str,
    worker_id: str,
    lease_token: str,
    result: Any,
    *,
    effect_key: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    occurred_at = _timestamp(now)
    effect_key = effect_key or task_id
    if not isinstance(effect_key, str) or not effect_key:
        raise TaskRuntimeError("effect_key must be a non-empty string")
    try:
        json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TaskRuntimeError("task result must be JSON serializable") from exc

    def mutate(state: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
        runtime = state[TASK_RUNTIME_KEY]
        generated: list[dict[str, Any]] = []
        _reconcile_new_events(runtime, events, generated, occurred_at)
        task = _task(runtime, task_id)
        if task.get("status") == "SUCCEEDED":
            if task.get("effect_key") != effect_key:
                raise TaskRuntimeError("task already completed with a different effect key")
            return state, generated, task
        _assert_lease(task, worker_id, lease_token)
        task["status"] = "SUCCEEDED"
        task["lease"] = None
        task["result"] = result
        task["effect_key"] = effect_key
        task["failure"] = None
        if state.get("current_task") == task_id:
            state["current_task"] = None
        generated.append(_event(events + generated, "TASK_SUCCEEDED", occurred_at, task_id=task_id, attempt=task["attempts"], effect_key=effect_key))
        return state, generated, task

    return _mutate(_project(root.resolve(), target), mutate)


def fail(
    root: Path,
    target: str,
    task_id: str,
    worker_id: str,
    lease_token: str,
    failure_class: str,
    message: str,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    occurred_at = _timestamp(now)
    root = root.resolve()
    policy, _, failure_classes, _ = _runtime_policy(root)
    retryable = set(policy["retryable_failure_classes"])
    terminal = set(policy["terminal_failure_classes"])
    if failure_class not in failure_classes or failure_class not in retryable | terminal:
        raise TaskRuntimeError(f"unknown task failure class: {failure_class}")
    if not isinstance(message, str) or not message.strip():
        raise TaskRuntimeError("failure message must be a non-empty string")

    def mutate(state: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
        runtime = state[TASK_RUNTIME_KEY]
        generated: list[dict[str, Any]] = []
        _reconcile_new_events(runtime, events, generated, occurred_at)
        task = _task(runtime, task_id)
        _assert_lease(task, worker_id, lease_token)
        attempts = task["attempts"]
        should_retry = failure_class in retryable and attempts < task["max_attempts"]
        next_status = "PENDING" if should_retry else "FAILED"
        task["status"] = next_status
        task["lease"] = None
        task["failure"] = {"class": failure_class, "message": message, "attempt": attempts, "occurred_at": occurred_at}
        if state.get("current_task") == task_id:
            state["current_task"] = None
        generated.append(
            _event(
                events + generated,
                "TASK_RETRY_SCHEDULED" if should_retry else "TASK_FAILED",
                occurred_at,
                task_id=task_id,
                attempt=attempts,
                failure_class=failure_class,
                retry_status=next_status,
                retry_exhausted=not should_retry and failure_class in retryable,
            )
        )
        _, blocked = _reconcile_new_events(runtime, events, generated, occurred_at)
        return state, generated, {"task_id": task_id, "status": next_status, "blocked": blocked}

    return _mutate(_project(root, target), mutate)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic project task DAG runtime.")
    parser.add_argument("target")
    parser.add_argument("command", choices=["init", "claim", "resume", "complete", "fail"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--now")
    parser.add_argument("--worker-id")
    parser.add_argument("--lease-token")
    parser.add_argument("--lease-seconds", type=int)
    parser.add_argument("--task-id")
    parser.add_argument("--effect-key")
    parser.add_argument("--result-json")
    parser.add_argument("--failure-class")
    parser.add_argument("--message")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize_runtime(args.root.resolve(), args.target, initialized_at=args.now)
        elif args.command == "claim":
            if not args.worker_id:
                parser.error("claim requires --worker-id")
            result = claim_next(args.root.resolve(), args.target, args.worker_id, now=args.now, lease_seconds=args.lease_seconds)
        elif args.command == "resume":
            result = resume(args.root.resolve(), args.target, now=args.now)
        elif args.command == "complete":
            if not all((args.task_id, args.worker_id, args.lease_token, args.result_json)):
                parser.error("complete requires --task-id, --worker-id, --lease-token, and --result-json")
            result = complete(
                args.root.resolve(),
                args.target,
                args.task_id,
                args.worker_id,
                args.lease_token,
                json.loads(args.result_json),
                effect_key=args.effect_key,
                now=args.now,
            )
        else:
            if not all((args.task_id, args.worker_id, args.lease_token, args.failure_class, args.message)):
                parser.error("fail requires --task-id, --worker-id, --lease-token, --failure-class, and --message")
            result = fail(
                args.root.resolve(),
                args.target,
                args.task_id,
                args.worker_id,
                args.lease_token,
                args.failure_class,
                args.message,
                now=args.now,
            )
    except (FileNotFoundError, TaskRuntimeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
