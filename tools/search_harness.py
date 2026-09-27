"""Validate and execute worker-proposed searches inside the harness boundary."""

from __future__ import annotations

import fcntl
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import log_event
from _common import load_json, read_jsonl
from canonical import canonical_sha256


class SearchHarnessError(ValueError):
    """A worker search request cannot be safely executed or recorded."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


def _validator(protocol_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    registry = Registry().with_resources([
        (common["$id"], Resource.from_contents(common)),
        (schema["$id"], Resource.from_contents(schema)),
    ])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate(protocol_root: Path, name: str, value: dict[str, Any]) -> None:
    errors = sorted(_validator(protocol_root, name).iter_errors(value), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.absolute_path) or "$"
        raise SearchHarnessError("SEARCH-REQUEST-SCHEMA", f"{name}#{field}: {error.message}")


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise SearchHarnessError("SEARCH-REQUEST-SCHEMA", "occurred_at must be RFC 3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SearchHarnessError("SEARCH-REQUEST-SCHEMA", "occurred_at must include a timezone")
    return value


def _request_hash(request: dict[str, Any]) -> str:
    return canonical_sha256(request)


def _execute_fake(request: dict[str, Any], occurred_at: str) -> dict[str, Any]:
    payload = {
        "adapter": request["adapter"],
        "query": request["query"],
        "question_id": request["question_id"],
        "strategy_id": request["strategy_id"],
    }
    return {
        "schema_version": "1.0.0",
        "request_id": request["request_id"],
        "status": "SUCCEEDED",
        "started_at": occurred_at,
        "finished_at": occurred_at,
        "result_sha256": canonical_sha256(payload),
        "result_count": 0,
        "failure": None,
    }


def execute_search_request(
    request: dict[str, Any],
    *,
    protocol_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    worker_id: str,
    occurred_at: str,
) -> dict[str, Any]:
    """Execute one typed request without giving the worker runtime access."""

    _validate(protocol_root.resolve(), "search-attempt-request", request)
    expected = {
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
    }
    if any(request.get(key) != value for key, value in expected.items()):
        raise SearchHarnessError("SEARCH-REQUEST-IDENTITY", "search request does not belong to the current attempt")
    if not isinstance(worker_id, str) or not worker_id:
        raise SearchHarnessError("SEARCH-REQUEST-IDENTITY", "worker_id is required")
    timestamp = _timestamp(occurred_at)
    if request["adapter"] != "fake":
        raise SearchHarnessError("SEARCH-ADAPTER", f"unsupported search adapter: {request['adapter']}")
    result = _execute_fake(request, timestamp)
    _validate(protocol_root.resolve(), "search-attempt-result", result)
    return result


def _event_id(request: dict[str, Any]) -> str:
    digest = hashlib.sha256(_request_hash(request).encode("ascii")).hexdigest()[:24]
    return f"SEARCH-{digest}"


def _same_event(existing: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(existing.get(key) == value for key, value in expected.items())


def _append_event_idempotently(log_path: Path, event: dict[str, Any]) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing = read_jsonl(log_path)
        for prior in existing:
            if prior.get("event_type") != "SEARCH_ATTEMPT":
                continue
            if prior.get("request_id") == event["request_id"]:
                if _same_event(prior, event):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    return prior
                raise SearchHarnessError("SEARCH-DUPLICATE-CONFLICT", "request_id already has a different recorded request")
            if prior.get("attempt_id") == event["attempt_id"] and prior.get("request_sha256") == event["request_sha256"]:
                if _same_event(prior, event):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    return prior
                raise SearchHarnessError("SEARCH-DUPLICATE-CONFLICT", "attempt and request hash have conflicting records")
        handle.seek(0, 2)
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return event


def record_search_requests(
    requests: Iterable[dict[str, Any]],
    *,
    protocol_root: Path,
    project_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    worker_id: str,
    occurred_at: str,
) -> list[dict[str, Any]]:
    """Execute and durably record all requests, returning idempotent events."""

    request_list = list(requests)
    seen: set[str] = set()
    executed: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for request in request_list:
        if not isinstance(request, dict):
            raise SearchHarnessError("SEARCH-REQUEST-SCHEMA", "search_requests must contain objects")
        request_id = request.get("request_id")
        if request_id in seen:
            raise SearchHarnessError("SEARCH-DUPLICATE-CONFLICT", "a result contains the same request_id more than once")
        seen.add(str(request_id))
        result = execute_search_request(
            request,
            protocol_root=protocol_root,
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            worker_id=worker_id,
            occurred_at=occurred_at,
        )
        executed.append((request, result, _request_hash(request)))

    log_path = project_root.resolve() / "07_runtime" / "run-log.jsonl"
    events: list[dict[str, Any]] = []
    for request, result, request_hash in executed:
        event = {
            "event_id": _event_id(request),
            "event_type": "SEARCH_ATTEMPT",
            "occurred_at": occurred_at,
            "question_id": request["question_id"],
            "strategy_id": request["strategy_id"],
            "worker_id": worker_id,
            "run_id": run_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "request_id": request["request_id"],
            "request_sha256": request_hash,
            "execution_status": result["status"],
            "result_sha256": result["result_sha256"],
            "result_count": result["result_count"],
        }
        events.append(_append_event_idempotently(log_path, event))
    return events


__all__ = ["SearchHarnessError", "execute_search_request", "record_search_requests"]
