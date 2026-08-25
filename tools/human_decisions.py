"""Persist, enumerate, and resolve typed human decision requests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import atomic_write_text, load_json, load_yaml, stable_json
from canonical import canonical_sha256
import task_runtime


DECISIONS_RELATIVE = Path("07_runtime/human-decisions.yaml")
CATEGORY_VALUES = {
    "PERSONAL_DATA_ACCESS",
    "EXTERNAL_ACTION",
    "ACCESS_CLASSIFICATION_CHANGE",
    "RIGHTS_OR_SAFETY_UNCERTAINTY",
    "CENTRAL_PROPOSITION_CHANGE",
    "SCOPE_OR_BUDGET_EXPANSION",
}
ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s\"'])(/(?:[^\s\"']+/)*[^\s\"']*)|\b[A-Za-z]:[\\/][^\s\"']*")


class HumanDecisionError(ValueError):
    """A human decision contract or state transition is unsafe."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


def _project(root: Path, project_id: str) -> Path:
    if not isinstance(project_id, str) or not project_id.startswith("project/") or project_id.count("/") != 1:
        raise HumanDecisionError("HUMAN-DECISION-STATE", "project_id must be project/<slug>")
    project = (root.resolve() / "projects" / project_id.split("/", 1)[1]).resolve()
    if project.parent != (root.resolve() / "projects") or not project.is_dir():
        raise FileNotFoundError(f"project not found: {project_id}")
    return project


def _schema_validator(protocol_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    registry = Registry().with_resources([(common["$id"], Resource.from_contents(common))])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate_schema(protocol_root: Path, name: str, value: dict[str, Any]) -> None:
    errors = sorted(_schema_validator(protocol_root, name).iter_errors(value), key=lambda error: tuple(error.absolute_path))
    if errors:
        raise HumanDecisionError("HUMAN-DECISION-CATEGORY" if name == "human-decision-request" else "HUMAN-DECISION-STATE", f"{name} schema is invalid")


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _security_check(value: dict[str, Any]) -> None:
    for text in _walk_strings(value):
        if "PRIVATE_RAW" in text or "RESTRICTED" in text or ABSOLUTE_PATH_RE.search(text):
            raise HumanDecisionError("HUMAN-DECISION-SECURITY", "decision text contains a private marker or absolute local path")


def _without_hash(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _validate_request(protocol_root: Path, request: dict[str, Any]) -> dict[str, Any]:
    _security_check(request)
    if request.get("human_decision_category") not in CATEGORY_VALUES:
        raise HumanDecisionError("HUMAN-DECISION-CATEGORY", "category is not one of the six approved categories")
    _validate_schema(protocol_root, "human-decision-request", request)
    expected_hash = canonical_sha256(_without_hash(request, "request_sha256"))
    if request.get("request_sha256") != expected_hash:
        raise HumanDecisionError("HUMAN-DECISION-STALE", "request_sha256 does not match the canonical request")
    option_ids = {str(option["id"]) for option in request["options"]}
    recommended = request.get("recommended_option")
    if recommended is not None and recommended not in option_ids:
        raise HumanDecisionError("HUMAN-DECISION-OPTION", "recommended_option is not present in options")
    return request


def _validate_response(protocol_root: Path, response: dict[str, Any]) -> dict[str, Any]:
    _security_check(response)
    _validate_schema(protocol_root, "human-decision-response", response)
    expected_hash = canonical_sha256(_without_hash(response, "response_sha256"))
    if response.get("response_sha256") != expected_hash:
        raise HumanDecisionError("HUMAN-DECISION-STALE", "response_sha256 does not match the canonical response")
    return response


def _load_document(project: Path) -> tuple[Path, dict[str, Any], str | None]:
    path = project / DECISIONS_RELATIVE
    if not path.exists():
        return path, {"schema_version": "1.0.0", "requests": [], "responses": []}, None
    try:
        value = load_yaml(path) or {}
    except Exception as exc:
        raise HumanDecisionError("HUMAN-DECISION-STATE", "human decision document cannot be read") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "1.0.0" or not isinstance(value.get("requests"), list) or not isinstance(value.get("responses"), list):
        raise HumanDecisionError("HUMAN-DECISION-STATE", "human decision document has an invalid shape")
    return path, value, path.read_text(encoding="utf-8")


def _write_document(path: Path, document: dict[str, Any]) -> None:
    atomic_write_text(path, yaml.safe_dump(document, sort_keys=False, allow_unicode=True))


def build_request(
    result_request: dict[str, Any],
    *,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    created_at: str,
) -> dict[str, Any]:
    if not isinstance(result_request, dict):
        raise HumanDecisionError("HUMAN-DECISION-CATEGORY", "worker human decision request must be an object")
    request = dict(result_request)
    if request.get("human_decision_category") not in CATEGORY_VALUES:
        raise HumanDecisionError("HUMAN-DECISION-CATEGORY", "category is not one of the six approved categories")
    for field, expected in (("project_id", project_id), ("task_id", task_id)):
        if field in request and request[field] != expected:
            raise HumanDecisionError("HUMAN-DECISION-STATE", f"worker request {field} does not match the attempt")
    request.update({
        "schema_version": "1.0.0",
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "created_at": created_at,
    })
    request["request_sha256"] = canonical_sha256(_without_hash(request, "request_sha256"))
    return request


def record_request(
    *,
    protocol_root: Path,
    work_root: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    request = _validate_request(protocol_root.resolve(), request)
    project = _project(work_root, request["project_id"])
    path, document, before = _load_document(project)
    for existing in document["requests"]:
        if not isinstance(existing, dict) or existing.get("id") != request["id"]:
            continue
        if existing == request:
            return existing
        raise HumanDecisionError("HUMAN-DECISION-REPLAY", "decision ID already contains a different request")
    if any(isinstance(existing, dict) and existing.get("request_sha256") == request["request_sha256"] for existing in document["requests"]):
        raise HumanDecisionError("HUMAN-DECISION-REPLAY", "request hash is already bound to another decision ID")
    document["requests"].append(request)
    document["requests"].sort(key=lambda item: str(item.get("id")))
    try:
        _write_document(path, document)
    except Exception:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, before)
        raise
    return request


def record_human_required(
    *,
    protocol_root: Path,
    work_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    worker_id: str,
    lease_token: str,
    result_request: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    request = build_request(
        result_request,
        project_id=project_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        created_at=now,
    )
    project = _project(work_root, project_id)
    path, _, before = _load_document(project)
    stored = record_request(protocol_root=protocol_root, work_root=work_root, request=request)
    try:
        runtime = task_runtime.load_runtime(work_root, project_id)
        task = runtime.get("tasks", {}).get(task_id, {}) if isinstance(runtime, dict) else {}
        pending = task.get("human_decision_request") if isinstance(task, dict) else None
        if task.get("status") == "WAITING_HUMAN":
            if isinstance(pending, dict) and pending.get("id") == stored["id"] and pending.get("request_sha256") == stored["request_sha256"]:
                return {
                    "request": stored,
                    "task": {"task_id": task_id, "status": "WAITING_HUMAN", "request_id": stored["id"], "idempotent": True},
                }
            raise HumanDecisionError("HUMAN-DECISION-STATE", "task is already waiting for a different decision request")
        runtime_result = task_runtime.wait_for_human(
            work_root,
            project_id,
            task_id,
            worker_id,
            lease_token,
            stored,
            now=now,
        )
    except Exception:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, before)
        raise
    runtime_result["idempotent"] = False
    return {"request": stored, "task": runtime_result}


def unresolved_requests(*, work_root: Path, project_id: str) -> list[dict[str, Any]]:
    project = _project(work_root, project_id)
    _, document, _ = _load_document(project)
    resolved = {str(item.get("decision_id")) for item in document["responses"] if isinstance(item, dict)}
    return sorted(
        [item for item in document["requests"] if isinstance(item, dict) and item.get("id") not in resolved],
        key=lambda item: str(item.get("id")),
    )


def resolve_request(
    *,
    protocol_root: Path,
    work_root: Path,
    project_id: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    response = _validate_response(protocol_root.resolve(), response)
    if response.get("project_id") != project_id:
        raise HumanDecisionError("HUMAN-DECISION-STATE", "response belongs to another project")
    project = _project(work_root, project_id)
    path, document, before = _load_document(project)
    request = next((item for item in document["requests"] if isinstance(item, dict) and item.get("id") == response.get("decision_id")), None)
    if request is None:
        raise HumanDecisionError("HUMAN-DECISION-STALE", "decision request does not exist")
    _validate_request(protocol_root.resolve(), request)
    for field in ("run_id", "task_id", "attempt_id"):
        if response.get(field) != request.get(field):
            raise HumanDecisionError("HUMAN-DECISION-STATE", f"response {field} does not match the request")
    if response.get("request_sha256") != request.get("request_sha256"):
        raise HumanDecisionError("HUMAN-DECISION-STALE", "response is bound to a stale request hash")
    option_ids = {str(item["id"]) for item in request["options"]}
    selected = response.get("selected_option")
    if response.get("action") != "SELECT" and selected is not None:
        raise HumanDecisionError("HUMAN-DECISION-OPTION", "selected_option is allowed only for SELECT responses")
    if selected is not None and selected not in option_ids:
        raise HumanDecisionError("HUMAN-DECISION-OPTION", "selected_option is not present in the request")
    existing = [item for item in document["responses"] if isinstance(item, dict) and item.get("decision_id") == response["decision_id"]]
    if existing:
        if existing[0] == response:
            return {"response": response, "task": {"status": "PENDING"}, "idempotent": True}
        raise HumanDecisionError("HUMAN-DECISION-REPLAY", "decision request has already been resolved differently")
    runtime_before = (project / "07_runtime/research-state.json").read_text(encoding="utf-8")
    log_before = (project / "07_runtime/run-log.jsonl").read_text(encoding="utf-8")
    try:
        task_result = task_runtime.resolve_human(
            work_root,
            project_id,
            request["task_id"],
            request["id"],
            request["request_sha256"],
            response,
            now=response["resolved_at"],
        )
        document["responses"].append(response)
        document["responses"].sort(key=lambda item: str(item.get("id")))
        _write_document(path, document)
    except Exception:
        atomic_write_text(project / "07_runtime/research-state.json", runtime_before)
        atomic_write_text(project / "07_runtime/run-log.jsonl", log_before)
        if before is None:
            path.unlink(missing_ok=True)
        elif before is not None:
            atomic_write_text(path, before)
        raise
    return {"response": response, "task": task_result, "idempotent": False}


def list_json(*, work_root: Path, project_id: str) -> str:
    return stable_json({"project_id": project_id, "requests": unresolved_requests(work_root=work_root, project_id=project_id)})


__all__ = [
    "HumanDecisionError",
    "build_request",
    "list_json",
    "record_human_required",
    "record_request",
    "resolve_request",
    "unresolved_requests",
]
