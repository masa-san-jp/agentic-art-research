"""Bounded, resumable supervisor loop for one isolated project run."""

from __future__ import annotations

import fcntl
import hashlib
import json
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import acceptance_executor
import human_decisions
import next_action
import task_runtime
import worker_adapter
from _common import atomic_write_text, load_json, load_yaml, stable_json
from attempt_workspace import AttemptWorkspace, create_attempt_workspace, load_attempt, snapshot_project
from canonical import canonical_sha256


JOURNAL_SCHEMA = "harness-journal"
JOURNAL_RELATIVE = Path(".harness/supervisor")
WORKER_FAILURES = {
    "WORKER-TIMEOUT": "TIMEOUT",
    "WORKER-EXIT": "TRANSIENT",
    "WORKER-COMMAND": "PERMANENT",
    "WORKER-PROTOCOL": "PERMANENT",
    "WORKER-OUTPUT-LIMIT": "VALIDATION",
    "WORKER-SECRET-OUTPUT": "AUTHORIZATION",
    "WORKER-CAPABILITY": "AUTHORIZATION",
}


class SupervisorError(RuntimeError):
    """A supervisor boundary or bounded-loop failure."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SupervisorError("HARNESS-JOURNAL", f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SupervisorError("HARNESS-JOURNAL", "timestamp must include a timezone")
    return parsed


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _add_seconds(value: str, seconds: float) -> str:
    return (_timestamp(value) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in value):
        raise SupervisorError("HARNESS-JOURNAL", f"{label} is not a safe identifier")
    return value


def _schema_validator(protocol_root: Path) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas" / "harness-journal.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    registry = Registry().with_resources([
        (common["$id"], Resource.from_contents(common)),
        (schema["$id"], Resource.from_contents(schema)),
    ])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate_journal(protocol_root: Path, journal: dict[str, Any]) -> None:
    errors = sorted(_schema_validator(protocol_root).iter_errors(journal), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.absolute_path) or "$"
        raise SupervisorError("HARNESS-JOURNAL", f"journal#{field}: {error.message}")


def _supervisor_config(protocol_root: Path) -> dict[str, Any]:
    policy = load_yaml(protocol_root / "config" / "stopping-policy.yaml") or {}
    defaults = policy.get("defaults") if isinstance(policy, dict) else None
    config = defaults.get("supervisor") if isinstance(defaults, dict) else None
    if not isinstance(config, dict):
        raise SupervisorError("HARNESS-JOURNAL", "stopping policy supervisor configuration is missing")
    for key in ("max_runtime_seconds", "max_tasks", "max_consecutive_no_progress", "shutdown_grace_seconds"):
        if isinstance(config.get(key), bool) or not isinstance(config.get(key), int) or config[key] <= 0:
            raise SupervisorError("HARNESS-JOURNAL", f"supervisor {key} must be a positive integer")
    backoff = config.get("retry_backoff_seconds")
    if not isinstance(backoff, dict):
        raise SupervisorError("HARNESS-JOURNAL", "supervisor retry_backoff_seconds must be a mapping")
    for failure_class, values in backoff.items():
        if not isinstance(values, list) or not values or any(isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0 for item in values):
            raise SupervisorError("HARNESS-JOURNAL", f"retry backoff for {failure_class} must be non-negative numbers")
    worker_map = config.get("worker_failure_classes")
    if not isinstance(worker_map, dict) or any(key not in WORKER_FAILURES or not isinstance(value, str) for key, value in worker_map.items()):
        raise SupervisorError("HARNESS-JOURNAL", "worker_failure_classes contains an unsupported mapping")
    return config


def _journal_path(work_root: Path, project_id: str, run_id: str) -> Path:
    slug = project_id.split("/", 1)[1] if project_id.startswith("project/") else project_id
    return work_root.resolve() / JOURNAL_RELATIVE / slug / f"{run_id}.json"


def _run_lock_path(work_root: Path, project_id: str, run_id: str) -> Path:
    key = hashlib.sha256(f"{project_id}|{run_id}".encode("utf-8")).hexdigest()
    return work_root.resolve() / ".harness" / "locks" / f"supervisor-{key}.lock"


@contextmanager
def _run_lock(work_root: Path, project_id: str, run_id: str) -> Iterator[None]:
    path = _run_lock_path(work_root, project_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("a+", encoding="utf-8")
    except OSError as exc:
        raise SupervisorError("HARNESS-RUN-LOCKED", "supervisor run lock cannot be opened") from exc
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SupervisorError("HARNESS-RUN-LOCKED", "another supervisor owns this project/run") from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _journal_id(project_id: str, run_id: str) -> str:
    return "HJ" + hashlib.sha256(f"{project_id}|{run_id}".encode("utf-8")).hexdigest()[:16]


def _resume_command(project_id: str, run_id: str) -> str:
    return f"python3 tools/harness.py resume {project_id} --run-id {run_id}"


def _empty_journal(project_id: str, run_id: str, supervisor_id: str, started_at: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "journal_id": _journal_id(project_id, run_id),
        "project_id": project_id,
        "run_id": run_id,
        "supervisor_id": supervisor_id,
        "status": "RUNNING",
        "phase": "RECONCILE",
        "task_id": None,
        "attempt_id": None,
        "lease": None,
        "attempt": None,
        "retry": None,
        "human_wait": None,
        "limits": {
            "started_at": started_at,
            "max_runtime_seconds": int(config["max_runtime_seconds"]),
            "max_tasks": int(config["max_tasks"]),
            "processed_tasks": 0,
        },
        "resume_command": _resume_command(project_id, run_id),
        "events": [],
    }


def _token_hash(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _event(journal: dict[str, Any], occurred_at: str, phase: str, **fields: Any) -> dict[str, Any]:
    event = {
        "seq": len(journal["events"]) + 1,
        "occurred_at": occurred_at,
        "phase": phase,
        "task_id": fields.pop("task_id", journal.get("task_id")),
        "attempt_id": fields.pop("attempt_id", journal.get("attempt_id")),
    }
    event.update({key: value for key, value in fields.items() if value is not None})
    return event


def _hash_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class Supervisor:
    """Run one project through the existing typed harness boundaries."""

    def __init__(
        self,
        *,
        protocol_root: Path,
        work_root: Path,
        output_root: Path,
        project_id: str,
        run_id: str,
        worker_id: str = "supervisor",
        adapter: str = "fake",
        command: Sequence[str] | None = None,
        fixture_mode: str | None = None,
        max_runtime_seconds: int | None = None,
        max_tasks: int | None = None,
        now: Callable[[], str] | None = None,
        sleep: Callable[[float], None] | None = None,
        worker_runner: Callable[..., dict[str, Any]] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if not project_id.startswith("project/") or project_id.count("/") != 1:
            raise SupervisorError("HARNESS-JOURNAL", "project_id must be project/<slug>")
        if not run_id.startswith("HR") or not run_id[2:].isdigit():
            raise SupervisorError("HARNESS-JOURNAL", "run_id must match HR followed by digits")
        self.protocol_root = protocol_root.resolve()
        self.work_root = work_root.resolve()
        self.output_root = output_root.resolve()
        self.project_id = project_id
        self.run_id = run_id
        self.worker_id = _safe_id(worker_id, "worker_id")
        self.adapter = adapter
        self.command = tuple(command) if command is not None else None
        self.fixture_mode = fixture_mode
        self.config = _supervisor_config(self.protocol_root)
        self.max_runtime_seconds = max_runtime_seconds or int(self.config["max_runtime_seconds"])
        self.max_tasks = max_tasks or int(self.config["max_tasks"])
        if self.max_runtime_seconds <= 0 or self.max_tasks <= 0:
            raise SupervisorError("HARNESS-NO-PROGRESS", "supervisor bounds must be positive")
        self.now = now or _now_text
        self.sleep = sleep or time.sleep
        self.worker_runner = worker_runner or worker_adapter.run_attempt
        self.event_callback = event_callback
        self.shutdown_requested = False
        self._journal: dict[str, Any] | None = None
        self._current_attempt: AttemptWorkspace | None = None

    @property
    def journal_path(self) -> Path:
        return _journal_path(self.work_root, self.project_id, self.run_id)

    def _save(self) -> None:
        if self._journal is None:
            raise SupervisorError("HARNESS-JOURNAL", "journal is not initialized")
        _validate_journal(self.protocol_root, self._journal)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.journal_path, stable_json(self._journal))

    def _load_or_create(self, resume: bool) -> dict[str, Any]:
        path = self.journal_path
        if path.exists():
            try:
                journal = load_json(path)
            except Exception as exc:
                raise SupervisorError("HARNESS-JOURNAL", "supervisor journal cannot be read") from exc
            if not isinstance(journal, dict):
                raise SupervisorError("HARNESS-JOURNAL", "supervisor journal must be an object")
            _validate_journal(self.protocol_root, journal)
            if journal.get("project_id") != self.project_id or journal.get("run_id") != self.run_id:
                raise SupervisorError("HARNESS-JOURNAL", "journal identity does not match the requested run")
            if not resume and journal.get("status") not in {"SHUTDOWN", "RUNNING"}:
                return journal
            if journal.get("status") in {"SUCCEEDED", "FAILED", "BLOCKED", "PAUSED", "NO_TASK_READY"}:
                return journal
            self._journal = journal
            self._journal["status"] = "RUNNING"
            self._journal["phase"] = "RECONCILE"
            self._journal["retry"] = None
            self._journal["human_wait"] = None
            self._save()
            return self._journal
        if resume:
            raise SupervisorError("HARNESS-JOURNAL", "resume requested but no supervisor journal exists")
        started_at = self.now()
        _timestamp(started_at)
        self._journal = _empty_journal(self.project_id, self.run_id, self.worker_id, started_at, self.config)
        self._journal["limits"]["max_runtime_seconds"] = self.max_runtime_seconds
        self._journal["limits"]["max_tasks"] = self.max_tasks
        self._save()
        return self._journal

    def _record(self, phase: str, *, now: str | None = None, status: str | None = None, **fields: Any) -> None:
        if self._journal is None:
            raise SupervisorError("HARNESS-JOURNAL", "journal is not initialized")
        current = now or self.now()
        self._journal["phase"] = phase
        if status is not None:
            self._journal["status"] = status
        self._journal["events"].append(_event(self._journal, current, phase, **fields))
        self._save()
        if self.event_callback is not None:
            self.event_callback(self._journal["events"][-1])

    def _set_lease(self, lease: dict[str, Any] | None) -> None:
        self._journal["lease"] = None if lease is None else {
            "owner": lease["owner"],
            "expires_at": lease["expires_at"],
            "token_sha256": _token_hash(lease["token"]),
        }

    def _check_bounds(self) -> None:
        limits = self._journal["limits"]
        elapsed = (_timestamp(self.now()) - _timestamp(limits["started_at"])).total_seconds()
        if elapsed > limits["max_runtime_seconds"]:
            raise SupervisorError("HARNESS-NO-PROGRESS", "supervisor max runtime was reached")
        if limits["processed_tasks"] >= limits["max_tasks"]:
            raise SupervisorError("HARNESS-NO-PROGRESS", "supervisor max task count was reached")

    def _attempt_id(self, task_id: str) -> str:
        runtime = task_runtime.load_runtime(self.work_root, self.project_id)
        attempts = int(runtime["tasks"][task_id]["attempts"])
        return f"AT{attempts:03d}"

    def _attempt(self, task_id: str, attempt_id: str, role: str) -> AttemptWorkspace:
        path = self.work_root / ".harness" / "attempts" / self.run_id / task_id / attempt_id
        if path.exists():
            return load_attempt(path, project_id=self.project_id, run_id=self.run_id, task_id=task_id, attempt_id=attempt_id, role=role)
        return create_attempt_workspace(
            protocol_root=self.protocol_root,
            work_root=self.work_root,
            output_root=self.output_root,
            project_id=self.project_id,
            run_id=self.run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            role=role,
        )

    def _recover_journal_attempt(self) -> None:
        task_id = self._journal.get("task_id") if self._journal else None
        attempt_id = self._journal.get("attempt_id") if self._journal else None
        if not isinstance(task_id, str) or not isinstance(attempt_id, str):
            return
        root = self.work_root / ".harness" / "attempts" / self.run_id / task_id / attempt_id
        metadata_path = root / "metadata.json"
        transaction_path = root / "transaction.json"
        if not root.is_dir() or not metadata_path.is_file() or not transaction_path.is_file():
            return
        metadata = load_json(metadata_path)
        role = metadata.get("role") if isinstance(metadata, dict) else None
        if not isinstance(role, str):
            raise SupervisorError("HARNESS-JOURNAL", "attempt metadata has no role")
        attempt = load_attempt(root, project_id=self.project_id, run_id=self.run_id, task_id=task_id, attempt_id=attempt_id, role=role)
        acceptance_executor.recover_attempt_transaction(attempt, protocol_root=self.protocol_root, work_root=self.work_root)

    def _request(self, action: dict[str, Any], attempt: AttemptWorkspace, attempt_id: str, now: str) -> dict[str, Any]:
        timeout = float(self.config.get("worker_timeout_seconds", 300))
        instructions = []
        for section in action.get("instructions") or []:
            if isinstance(section, dict):
                instructions.append(f"## {section.get('section')}. {section.get('title')}\n{section.get('body')}")
            else:
                instructions.append(str(section))
        targets = []
        for target in action.get("write_targets") or []:
            if isinstance(target, dict) and isinstance(target.get("path"), str):
                targets.append({"path": target["path"], "mode": target.get("mode", "UPDATE")})
        acceptance = action.get("acceptance") or []
        request: dict[str, Any] = {
            "schema_version": "1.0.0",
            "run_id": self.run_id,
            "attempt_id": attempt_id,
            "project_id": self.project_id,
            "task_id": action["task_id"],
            "role": action["role"],
            "lease": {"token": action["lease"]["token"], "expires_at": action["lease"]["expires_at"]},
            "context": {
                "summary": str((action.get("context") or {}).get("summary", "")) or "Task context.",
                "source_refs": list((action.get("context") or {}).get("source_refs") or []),
                "constraints": list((action.get("context") or {}).get("constraints") or []),
            },
            "instructions": instructions,
            "write_targets": targets,
            "acceptance_ids": [f"AT{index:03d}" for index, _ in enumerate(acceptance, 1)],
            "attempt_workspace": str(attempt.project),
            "deadline": _add_seconds(now, timeout),
            "capabilities": {"required": ["structured-output"], "declared": ["structured-output"], "environment_allowlist": []},
        }
        if self.fixture_mode:
            request["fixture_mode"] = self.fixture_mode
        return request

    def _heartbeat(self, task_id: str, worker_id: str, token: str) -> None:
        if self.shutdown_requested:
            raise worker_adapter.AttemptHeartbeatError("HARNESS-SHUTDOWN: shutdown requested")
        try:
            result = task_runtime.heartbeat(
                self.work_root,
                self.project_id,
                task_id,
                worker_id,
                token,
                now=self.now(),
                lease_seconds=int(self.config.get("heartbeat_lease_seconds", 1800)),
            )
        except Exception as exc:
            raise worker_adapter.AttemptHeartbeatError(f"HARNESS-HEARTBEAT: lease renewal failed: {exc}") from exc
        if self._current_attempt is not None:
            self._sync_runtime_snapshot(self._current_attempt)
        self._set_lease({"owner": worker_id, "token": token, "expires_at": result["lease_expires_at"]})
        self._record("HEARTBEAT", task_id=task_id, attempt_id=self._journal.get("attempt_id"), now=self.now())

    def _sync_runtime_snapshot(self, attempt: AttemptWorkspace) -> None:
        """Mirror supervisor-owned runtime changes into the attempt baseline."""

        canonical = self.work_root / "projects" / self.project_id.split("/", 1)[1]
        baseline = load_json(attempt.baseline_manifest)
        current = snapshot_project(
            canonical,
            project_id=self.project_id,
            run_id=self.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
            role=attempt.role,
            protocol_root=self.protocol_root,
        )
        baseline_entries = {entry["path"]: entry for entry in baseline.get("entries", [])}
        current_entries = {entry["path"]: entry for entry in current.get("entries", [])}
        runtime_paths = sorted({path for path in set(baseline_entries) | set(current_entries) if path == "07_runtime" or path.startswith("07_runtime/")})
        for relative in runtime_paths:
            source = canonical / relative
            destination = attempt.project / relative
            if relative not in current_entries:
                destination.unlink(missing_ok=True)
                baseline_entries.pop(relative, None)
                continue
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            baseline_entries[relative] = current_entries[relative]
        baseline["entries"] = sorted(baseline_entries.values(), key=lambda entry: entry["path"])
        baseline["file_count"] = sum(1 for entry in baseline["entries"] if entry["type"] == "file")
        baseline["total_bytes"] = sum(int(entry["size"]) for entry in baseline["entries"] if entry["type"] == "file")
        atomic_write_text(attempt.baseline_manifest, stable_json(baseline))

    def _worker(self, request_path: Path, task_id: str, token: str, result_path: Path) -> dict[str, Any]:
        interval = float(self.config.get("heartbeat_interval_seconds", 900))
        return self.worker_runner(
            request_path,
            adapter=self.adapter,
            protocol_root=self.protocol_root,
            command=self.command,
            output_path=result_path,
            heartbeat_callback=lambda: self._heartbeat(task_id, self.worker_id, token),
            heartbeat_interval_seconds=interval,
            now=lambda: _timestamp(self.now()),
        )

    def _failure_class(self, result: dict[str, Any]) -> str:
        failure = result.get("failure") or {}
        worker_class = failure.get("class") if isinstance(failure, dict) else None
        mapped = self.config.get("worker_failure_classes", {}).get(worker_class)
        if not isinstance(mapped, str):
            mapped = WORKER_FAILURES.get(worker_class, "PERMANENT")
        return mapped

    def _backoff(self, failure_class: str, attempt: int) -> float:
        values = self.config.get("retry_backoff_seconds", {}).get(failure_class, [0])
        return float(values[min(max(attempt - 1, 0), len(values) - 1)])

    def _sleep_backoff(self, failure_class: str, attempt: int, now: str) -> str:
        seconds = self._backoff(failure_class, attempt)
        next_at = _add_seconds(now, seconds)
        if seconds > 0:
            self.sleep(seconds)
        return next_at

    def _terminal_outcome(self) -> dict[str, Any]:
        runtime = task_runtime.load_runtime(self.work_root, self.project_id)
        tasks = list((runtime.get("tasks") or {}).values())
        if any(task.get("status") == "WAITING_HUMAN" for task in tasks):
            waiting = next(task for task in tasks if task.get("status") == "WAITING_HUMAN")
            request = waiting.get("human_decision_request") or {}
            self._journal["human_wait"] = {"request_id": request.get("id"), "request_sha256": request.get("request_sha256")}
            self._record("WAITING_HUMAN", status="PAUSED")
            return self._journal
        if tasks and all(task.get("status") == "SUCCEEDED" for task in tasks):
            self._record("TERMINAL", status="SUCCEEDED")
            return self._journal
        if any(task.get("status") == "FAILED" for task in tasks):
            self._record("TERMINAL", status="FAILED")
            return self._journal
        if any(task.get("status") == "BLOCKED" for task in tasks):
            self._record("TERMINAL", status="BLOCKED")
            return self._journal
        self._record("TERMINAL", status="NO_TASK_READY")
        return self._journal

    def _handle_failure(self, task_id: str, token: str, failure_class: str, message: str) -> None:
        try:
            result = task_runtime.fail(
                self.work_root,
                self.project_id,
                task_id,
                self.worker_id,
                token,
                failure_class,
                message,
                now=self.now(),
            )
        except task_runtime.TaskRuntimeError as exc:
            raise SupervisorError("HARNESS-WORKER-LOST", str(exc)) from exc
        attempt = int(task_runtime.load_runtime(self.work_root, self.project_id)["tasks"][task_id].get("attempts", 1))
        if result.get("status") == "PENDING":
            self._schedule_retry(task_id, failure_class, max(1, attempt + 1))
        else:
            self._journal["retry"] = None
            runtime_task = task_runtime.load_runtime(self.work_root, self.project_id)["tasks"][task_id]
            exhausted = failure_class in set(self.config.get("retry_backoff_seconds", {})) and int(runtime_task.get("attempts", 0)) >= int(runtime_task.get("max_attempts", 0))
            self._record("TERMINAL", status="FAILED", task_id=task_id, failure_class="HARNESS-RETRY-EXHAUSTED" if exhausted else failure_class)

    def _schedule_retry(self, task_id: str, failure_class: str, attempt: int) -> None:
        now = self.now()
        next_at = self._sleep_backoff(failure_class, attempt, now)
        self._journal["retry"] = {"failure_class": failure_class, "attempt": attempt, "next_at": next_at}
        self._record("RETRY_WAIT", task_id=task_id, failure_class=failure_class, now=now)

    def _run_loop(self, resume: bool) -> dict[str, Any]:
        journal = self._load_or_create(resume)
        if journal.get("status") in {"SUCCEEDED", "FAILED", "BLOCKED", "PAUSED", "NO_TASK_READY"}:
            return journal
        previous_handlers: dict[int, Any] = {}

        def request_shutdown(signum: int, _frame: Any) -> None:
            self.shutdown_requested = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, request_shutdown)
        try:
            no_progress = 0
            while True:
                if self.shutdown_requested:
                    raise SupervisorError("HARNESS-SHUTDOWN", "shutdown requested before the next claim")
                self._check_bounds()
                self._record("RECONCILE")
                self._recover_journal_attempt()
                task_runtime.resume(self.work_root, self.project_id, now=self.now())
                preview = task_runtime.peek_next(self.work_root, self.project_id, self.worker_id, now=self.now())
                if preview is None:
                    return self._terminal_outcome()
                action = next_action.build_next_action(
                    self.work_root,
                    self.project_id,
                    self.worker_id,
                    self.now(),
                    protocol_root=self.protocol_root,
                    work_root=self.work_root,
                    output_root=self.output_root,
                )
                if action.get("status") not in {"TASK_CLAIMED", "TASK_RESUMED"}:
                    return self._terminal_outcome()
                task_id = action["task_id"]
                lease = action.get("lease") or {}
                token = lease.get("token")
                if not isinstance(token, str):
                    raise SupervisorError("HARNESS-WORKER-LOST", "claimed task has no lease token")
                attempt_id = self._attempt_id(task_id)
                previous_attempt_id = self._journal.get("attempt_id")
                if resume and isinstance(previous_attempt_id, str):
                    previous_result = self.work_root / ".harness" / "attempts" / self.run_id / task_id / previous_attempt_id / "result.json"
                    if previous_result.is_file():
                        # The runtime may mint a fresh lease after a crash,
                        # but a durable worker result belongs to the prior
                        # attempt and must be consumed exactly once.
                        attempt_id = previous_attempt_id
                self._journal["task_id"] = task_id
                self._journal["attempt_id"] = attempt_id
                self._journal["limits"]["processed_tasks"] += 1
                self._set_lease(lease)
                self._record("CLAIMED", task_id=task_id, attempt_id=attempt_id)
                attempt = self._attempt(task_id, attempt_id, action["role"])
                self._current_attempt = attempt
                transaction = attempt.root / "transaction.json"
                if transaction.exists():
                    acceptance_executor.recover_attempt_transaction(attempt, protocol_root=self.protocol_root, work_root=self.work_root)
                self._record("ATTEMPT_PREPARED", task_id=task_id, attempt_id=attempt_id)
                request_path = attempt.root / "request.json"
                result_path = attempt.root / "result.json"
                now = self.now()
                if not request_path.exists():
                    request = self._request(action, attempt, attempt_id, now)
                    atomic_write_text(request_path, stable_json(request))
                else:
                    request = load_json(request_path)
                request_hash = canonical_sha256(request)
                self._journal["attempt"] = {"request_sha256": request_hash, "result_sha256": None, "changeset_sha256": None, "acceptance_report_sha256": None, "effect_key": None}
                self._record("WORKER_RUNNING", task_id=task_id, attempt_id=attempt_id, request_sha256=request_hash)
                if result_path.exists():
                    self._sync_runtime_snapshot(attempt)
                    result = load_json(result_path)
                else:
                    try:
                        result = self._worker(request_path, task_id, token, result_path)
                    except worker_adapter.AttemptHeartbeatError as exc:
                        if self.shutdown_requested:
                            raise SupervisorError("HARNESS-SHUTDOWN", "shutdown requested during worker execution") from exc
                        self._current_attempt = None
                        self._handle_failure(task_id, token, "LEASE_EXPIRED", str(exc))
                        continue
                result_hash = canonical_sha256(result)
                self._journal["attempt"]["result_sha256"] = result_hash
                self._record("RESULT_RECORDED", task_id=task_id, attempt_id=attempt_id, request_sha256=request_hash, result_sha256=result_hash)
                status = result.get("status")
                if status == "HUMAN_REQUIRED":
                    waiting = human_decisions.record_human_required(
                        protocol_root=self.protocol_root,
                        work_root=self.work_root,
                        project_id=self.project_id,
                        run_id=self.run_id,
                        task_id=task_id,
                        attempt_id=attempt_id,
                        worker_id=self.worker_id,
                        lease_token=token,
                        result_request=result.get("human_decision_request"),
                        now=self.now(),
                    )
                    request_value = waiting["request"]
                    self._journal["human_wait"] = {"request_id": request_value["id"], "request_sha256": request_value["request_sha256"]}
                    self._set_lease(None)
                    self._record("WAITING_HUMAN", status="PAUSED", task_id=task_id, attempt_id=attempt_id, request_sha256=request_value["request_sha256"])
                    return self._journal
                if status == "FAILED":
                    self._handle_failure(task_id, token, self._failure_class(result), str((result.get("failure") or {}).get("message", "worker failed")))
                    continue
                if status != "SUCCEEDED":
                    self._handle_failure(task_id, token, "PERMANENT", "worker returned an unsupported status")
                    continue
                self._record("ACCEPTANCE", task_id=task_id, attempt_id=attempt_id, result_sha256=result_hash)
                try:
                    completed = acceptance_executor.complete_attempt(
                        attempt,
                        protocol_root=self.protocol_root,
                        work_root=self.work_root,
                        output_root=self.output_root,
                        worker_id=self.worker_id,
                        lease_token=token,
                        evaluated_at=self.now(),
                    )
                except Exception as exc:
                    self._handle_failure(task_id, token, "VALIDATION", str(exc))
                    continue
                report_hash = completed.get("report_sha256")
                if isinstance(self._journal.get("attempt"), dict):
                    self._journal["attempt"]["acceptance_report_sha256"] = report_hash
                    self._journal["attempt"]["changeset_sha256"] = completed.get("changeset_sha256")
                    self._journal["attempt"]["effect_key"] = completed.get("effect_key")
                self._record("PROMOTED" if completed.get("promoted") else "RETRY_WAIT", task_id=task_id, attempt_id=attempt_id, report_sha256=report_hash)
                if completed.get("task", {}).get("status") == "SUCCEEDED":
                    self._journal["retry"] = None
                    self._record("COMPLETED", status="RUNNING", task_id=task_id, attempt_id=attempt_id, report_sha256=report_hash)
                else:
                    failure = completed.get("task", {}).get("failure") or {}
                    failure_class = failure.get("class", "VALIDATION")
                    if completed.get("task", {}).get("status") == "PENDING":
                        self._schedule_retry(task_id, failure_class, max(1, int(completed.get("task", {}).get("attempts", 1)) + 1))
                    else:
                        self._journal["retry"] = None
                        self._record("TERMINAL", status="FAILED", task_id=task_id, failure_class="HARNESS-RETRY-EXHAUSTED" if failure_class == "VALIDATION" else failure_class)
                self._current_attempt = None
                no_progress = no_progress + 1 if self._journal["limits"]["processed_tasks"] >= self.max_tasks else 0
                if no_progress >= int(self.config["max_consecutive_no_progress"]):
                    raise SupervisorError("HARNESS-NO-PROGRESS", "supervisor made no bounded progress")
        finally:
            for sig, previous in previous_handlers.items():
                signal.signal(sig, previous)

    def run(self, *, resume: bool = False) -> dict[str, Any]:
        with _run_lock(self.work_root, self.project_id, self.run_id):
            try:
                return self._run_loop(resume)
            except SupervisorError as exc:
                if self._journal is not None:
                    if exc.rule == "HARNESS-SHUTDOWN":
                        self._record("SHUTDOWN", status="SHUTDOWN", failure_class=exc.rule)
                    else:
                        self._record("TERMINAL", status="BLOCKED", failure_class=exc.rule)
                if exc.rule == "HARNESS-SHUTDOWN":
                    return self._journal or {"status": "SHUTDOWN", "resume_command": _resume_command(self.project_id, self.run_id)}
                raise
            except (worker_adapter.AttemptHeartbeatError, worker_adapter.WorkerAdapterError) as exc:
                if isinstance(exc, worker_adapter.AttemptHeartbeatError) and self.shutdown_requested:
                    if self._journal is not None:
                        self._record("SHUTDOWN", status="SHUTDOWN", failure_class="HARNESS-SHUTDOWN")
                    return self._journal or {"status": "SHUTDOWN", "resume_command": _resume_command(self.project_id, self.run_id)}
                if self._journal is not None:
                    self._record("TERMINAL", status="BLOCKED", failure_class="HARNESS-HEARTBEAT" if isinstance(exc, worker_adapter.AttemptHeartbeatError) else "HARNESS-WORKER-LOST")
                raise SupervisorError("HARNESS-HEARTBEAT" if isinstance(exc, worker_adapter.AttemptHeartbeatError) else "HARNESS-WORKER-LOST", str(exc)) from exc


def run_supervisor(**kwargs: Any) -> dict[str, Any]:
    return Supervisor(**kwargs).run(resume=False)


def resume_supervisor(**kwargs: Any) -> dict[str, Any]:
    return Supervisor(**kwargs).run(resume=True)


__all__ = ["Supervisor", "SupervisorError", "resume_supervisor", "run_supervisor"]
