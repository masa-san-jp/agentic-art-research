"""Hash-chained, privacy-safe observability for one harness run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import load_json, read_jsonl, stable_json
from canonical import canonical_sha256


EVENT_SCHEMA = "harness-event"
EVENT_RELATIVE = Path(".harness/events")
PUBLIC_PHASES = (
    "PREFLIGHT",
    "BOOTSTRAPPED",
    "RUNNING",
    "COMPLETING",
    "BUILDING_HANDOFF",
    "EXPORTING",
    "PUBLISHING",
    "COMPLETE",
)
PHASE_INDEX = {phase: index for index, phase in enumerate(PUBLIC_PHASES)}
SUPERVISOR_EVENT_TYPES = {
    "RECONCILE": "PHASE_ENTERED",
    "CLAIMED": "TASK_CLAIMED",
    "ATTEMPT_PREPARED": "ATTEMPT_PREPARED",
    "WORKER_RUNNING": "WORKER_STARTED",
    "HEARTBEAT": "HEARTBEAT",
    "RESULT_RECORDED": "WORKER_RESULT",
    "ACCEPTANCE": "ACCEPTANCE",
    "PROMOTED": "TASK_PROMOTED",
    "COMPLETED": "TASK_COMPLETED",
    "RETRY_WAIT": "RETRY_SCHEDULED",
    "WAITING_HUMAN": "HUMAN_WAIT",
    "SHUTDOWN": "RUN_PAUSED",
    "TERMINAL": "RUN_TERMINAL",
}


class HarnessEventError(ValueError):
    """A named event stream integrity failure."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


def _timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HarnessEventError("HARNESS-EVENT-SCHEMA", f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HarnessEventError("HARNESS-EVENT-SCHEMA", "event timestamp must include a timezone")
    return parsed


def _schema_validator(protocol_root: Path) -> Draft202012Validator:
    try:
        common = load_json(protocol_root / "schemas" / "common.schema.json")
        schema = load_json(protocol_root / "schemas" / "harness-event.schema.json")
        registry = Registry().with_resources(
            [(common["$id"], Resource.from_contents(common)), (schema["$id"], Resource.from_contents(schema))]
        )
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, registry=registry)
    except Exception as exc:
        raise HarnessEventError("HARNESS-EVENT-SCHEMA", f"event schema configuration is invalid: {exc}") from exc


def _validate_schema(protocol_root: Path, value: dict[str, Any]) -> None:
    errors = sorted(
        _schema_validator(protocol_root).iter_errors(value),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.absolute_path) or "$"
        raise HarnessEventError("HARNESS-EVENT-SCHEMA", f"event#{field}: {error.message}")


def _event_without_hash(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in {"event_id", "event_sha256"}}


def _event_hash(event: dict[str, Any]) -> str:
    return canonical_sha256(_event_without_hash(event))


def _event_id(event: dict[str, Any]) -> str:
    return "HE" + hashlib.sha256(stable_json(_event_without_hash(event)).encode("utf-8")).hexdigest()[:16]


def _stream_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_sequence(protocol_root: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    previous: str | None = None
    ids: set[str] = set()
    last_phase_index = -1
    last_status: str | None = None
    tasks: dict[str, str] = {}
    for expected_seq, event in enumerate(events, 1):
        if not isinstance(event, dict):
            raise HarnessEventError("HARNESS-EVENT-SCHEMA", f"event {expected_seq} is not an object")
        _validate_schema(protocol_root, event)
        if event["seq"] != expected_seq:
            raise HarnessEventError("HARNESS-EVENT-ORDER", f"event sequence must be {expected_seq}, got {event['seq']}")
        if event["event_id"] in ids:
            raise HarnessEventError("HARNESS-EVENT-ORDER", f"duplicate event ID {event['event_id']}")
        ids.add(event["event_id"])
        if event["previous_event_sha256"] != previous:
            raise HarnessEventError("HARNESS-EVENT-HASH", f"event {expected_seq} previous hash does not match the stream")
        if event["event_id"] != _event_id(event) or event["event_sha256"] != _event_hash(event):
            raise HarnessEventError("HARNESS-EVENT-HASH", f"event {expected_seq} hash does not match its canonical bytes")
        phase_index = PHASE_INDEX[event["phase"]]
        if phase_index < last_phase_index:
            raise HarnessEventError("HARNESS-EVENT-ORDER", f"phase regressed at event {expected_seq}")
        last_phase_index = phase_index
        task_id = event.get("task_id")
        event_type = event["event_type"]
        if isinstance(task_id, str):
            if event_type == "TASK_COMPLETED":
                tasks[task_id] = "SUCCEEDED"
            elif event_type == "HUMAN_WAIT":
                tasks[task_id] = "WAITING_HUMAN"
            elif event_type == "RUN_FAILED":
                tasks[task_id] = "FAILED"
            elif event_type == "TASK_CLAIMED":
                tasks[task_id] = "RUNNING"
        if event.get("status") is not None:
            last_status = event["status"]
        previous = event["event_sha256"]
    return {
        "event_count": len(events),
        "phase": events[-1]["phase"] if events else None,
        "status": last_status,
        "tasks": tasks,
        "event_sha256": previous,
    }


def load_event_stream(protocol_root: Path, path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load and replay a JSONL stream, rejecting tampering and regressions."""

    try:
        events = read_jsonl(path)
    except Exception as exc:
        raise HarnessEventError("HARNESS-EVENT-SCHEMA", f"event stream cannot be read: {path}") from exc
    return events, _validate_sequence(protocol_root, events)


@dataclass
class EventStream:
    protocol_root: Path
    path: Path
    run_id: str
    project_id: str
    now: Callable[[], str]
    request_sha256: str | None = None
    worker_fingerprint: str | None = None

    def __post_init__(self) -> None:
        self.protocol_root = self.protocol_root.resolve()
        self.path = self.path.resolve()
        if self.path.exists():
            events, replay = load_event_stream(self.protocol_root, self.path)
            if events and (events[0].get("run_id") != self.run_id or events[0].get("project_id") != self.project_id):
                raise HarnessEventError("HARNESS-EVENT-ORDER", "event stream identity does not match the requested run")
            self._events = events
            self._previous = replay["event_sha256"]
        else:
            self._events = []
            self._previous = None

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def sha256(self) -> str | None:
        return _stream_hash(self.path) if self.path.is_file() else None

    def append(
        self,
        *,
        phase: str,
        event_type: str,
        occurred_at: str | None = None,
        task_id: str | None = None,
        attempt_id: str | None = None,
        status: str | None = None,
        failure_class: str | None = None,
        hashes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if phase not in PHASE_INDEX:
            raise HarnessEventError("HARNESS-EVENT-SCHEMA", f"unsupported public phase {phase!r}")
        if self._events and PHASE_INDEX[phase] < PHASE_INDEX[self._events[-1]["phase"]]:
            raise HarnessEventError("HARNESS-EVENT-ORDER", f"phase regressed from {self._events[-1]['phase']} to {phase}")
        event: dict[str, Any] = {
            "schema_version": "1.0.0",
            "event_id": "HE0000000000000000",
            "seq": len(self._events) + 1,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "phase": phase,
            "event_type": event_type,
            "occurred_at": occurred_at or self.now(),
            "hashes": {key: value for key, value in sorted((hashes or {}).items()) if value is not None},
            "previous_event_sha256": self._previous,
            "event_sha256": "sha256:" + "0" * 64,
        }
        if task_id is not None:
            event["task_id"] = task_id
        if attempt_id is not None:
            event["attempt_id"] = attempt_id
        if status is not None:
            event["status"] = status
        if failure_class is not None:
            event["failure_class"] = failure_class
        _timestamp(event["occurred_at"])
        event["event_id"] = _event_id(event)
        event["event_sha256"] = _event_hash(event)
        _validate_schema(self.protocol_root, event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with self.path.open("ab") as handle:
                handle.write(line)
                handle.flush()
        except OSError as exc:
            raise HarnessEventError("HARNESS-EVENT-SCHEMA", "event stream cannot be appended") from exc
        self._events.append(event)
        self._previous = event["event_sha256"]
        return event

    def append_supervisor_event(self, event: dict[str, Any], *, phase: str) -> dict[str, Any]:
        event_type = SUPERVISOR_EVENT_TYPES.get(str(event.get("phase")), "PHASE_ENTERED")
        hashes: dict[str, str] = {}
        for source, target in (
            ("request_sha256", "attempt_request_sha256"),
            ("result_sha256", "result_sha256"),
            ("report_sha256", "acceptance_report_sha256"),
        ):
            if isinstance(event.get(source), str):
                hashes[target] = event[source]
        return self.append(
            phase=phase,
            event_type=event_type,
            occurred_at=str(event.get("occurred_at") or self.now()),
            task_id=event.get("task_id"),
            attempt_id=event.get("attempt_id"),
            status=str(event["status"]) if event.get("status") else None,
            failure_class=str(event["failure_class"]) if event.get("failure_class") else None,
            hashes=hashes,
        )


def replay_event_stream(protocol_root: Path, path: Path) -> dict[str, Any]:
    """Return the reconstructed run state from the append-only stream."""

    _, replay = load_event_stream(protocol_root, path)
    return replay


__all__ = [
    "EVENT_RELATIVE",
    "EventStream",
    "HarnessEventError",
    "load_event_stream",
    "replay_event_stream",
]
