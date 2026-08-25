from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import atomic_write_text, iter_project_dirs, load_json, load_yaml, stable_json
from canonical import canonical_sha256
from new_project import create_project
from validate import validate_repository


REQUEST_RELATIVE = Path("00_intake/research-request.yaml")
RECEIPT_RELATIVE = Path("00_intake/research-request-receipt.yaml")
TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$")
ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^|[\s\"'(=:])(?:/(?!/)\S+|[A-Za-z]:[\\/]\S*|~[\\/]\S*|file://\S+)", re.IGNORECASE)
PROHIBITED_CLASSIFICATION_PATTERN = re.compile(r"(?<![A-Z0-9_])(?:PRIVATE_RAW|RESTRICTED)(?![A-Z0-9_])", re.IGNORECASE)


class RequestAcceptanceError(ValueError):
    """Raised when an upstream research request cannot be accepted safely."""


def _field_path(parts: Any) -> str:
    field = "$"
    for part in parts:
        field += f"[{part}]" if isinstance(part, int) else f".{part}"
    return field


def _validator(protocol_root: Path) -> Draft202012Validator:
    schema_path = protocol_root / "schemas" / "research-request.schema.json"
    common_path = protocol_root / "schemas" / "common.schema.json"
    try:
        schema = load_json(schema_path)
        common = load_json(common_path)
        Draft202012Validator.check_schema(schema)
        registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
        return Draft202012Validator(schema, registry=registry)
    except Exception as exc:
        raise RequestAcceptanceError(f"schema configuration is invalid: {exc}") from exc


def _load_input(path: Path) -> Any:
    if not path.is_file():
        raise RequestAcceptanceError(f"request input not found: {path}")
    if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise RequestAcceptanceError(f"request input must be .yaml, .yml, or .json: {path}")
    try:
        value = load_json(path) if path.suffix.lower() == ".json" else load_yaml(path)
    except Exception as exc:
        raise RequestAcceptanceError(f"{path}: cannot parse request: {exc}") from exc
    if not isinstance(value, dict):
        raise RequestAcceptanceError(f"{path}: request must be an object/mapping")
    return value


def _iter_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        result: list[tuple[str, str]] = []
        for key, child in value.items():
            result.extend(_iter_strings(child, f"{path}.{key}"))
        return result
    if isinstance(value, list):
        result = []
        for index, child in enumerate(value):
            result.extend(_iter_strings(child, f"{path}[{index}]"))
        return result
    return [(path, value)] if isinstance(value, str) else []


def _security_check(protocol_root: Path, request: dict[str, Any]) -> None:
    access = load_yaml(protocol_root / "config" / "access-policy.yaml") or {}
    handoff_policy = load_yaml(protocol_root / "config" / "handoff-policy.yaml") or {}
    secret_patterns: list[tuple[str, re.Pattern[str]]] = []
    for item in access.get("secret_patterns", []) if isinstance(access, dict) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("pattern"), str):
            continue
        try:
            secret_patterns.append((item["id"], re.compile(item["pattern"])))
        except re.error as exc:
            raise RequestAcceptanceError(f"invalid secret pattern {item['id']!r}: {exc}") from exc
    signed_markers = [str(item).lower() for item in handoff_policy.get("signed_url_markers", []) if isinstance(item, str)]
    for field, value in _iter_strings(request):
        if PROHIBITED_CLASSIFICATION_PATTERN.search(value):
            raise RequestAcceptanceError(f"REQUEST-SECURITY {field}: prohibited data classification marker")
        if ABSOLUTE_PATH_PATTERN.search(value) or "../" in value or "..\\" in value:
            raise RequestAcceptanceError(f"REQUEST-SECURITY {field}: absolute or traversal path")
        lowered = value.lower()
        if lowered.startswith("file:") or any(marker in lowered for marker in signed_markers):
            raise RequestAcceptanceError(f"REQUEST-SECURITY {field}: local or signed URL is not accepted")
        for pattern_id, regex in secret_patterns:
            if regex.search(value):
                raise RequestAcceptanceError(f"REQUEST-SECURITY {field}: secret pattern {pattern_id}")


def _validate_request(protocol_root: Path, path: Path) -> tuple[dict[str, Any], str]:
    value = _load_input(path)
    validator = _validator(protocol_root)
    errors = sorted(validator.iter_errors(value), key=lambda error: (tuple(error.absolute_path), error.validator, error.message))
    if errors:
        error = errors[0]
        raise RequestAcceptanceError(f"{path}#{_field_path(error.absolute_path)} [SCHEMA:{error.validator}] {error.message}")
    try:
        _security_check(protocol_root, value)
    except RequestAcceptanceError:
        raise
    except Exception as exc:
        raise RequestAcceptanceError(f"request security policy cannot be read: {exc}") from exc
    return value, canonical_sha256(value)


def _existing_requests(root: Path, protocol_root: Path | None = None) -> list[tuple[Path, dict[str, Any], str]]:
    records: list[tuple[Path, dict[str, Any], str]] = []
    protocol_root = protocol_root or root
    validator = _validator(protocol_root)
    for project in iter_project_dirs(root):
        path = project / REQUEST_RELATIVE
        if not path.is_file():
            continue
        value = _load_input(path)
        errors = sorted(validator.iter_errors(value), key=lambda error: (tuple(error.absolute_path), error.validator, error.message))
        if errors:
            error = errors[0]
            raise RequestAcceptanceError(
                f"existing request is invalid: {path}#{_field_path(error.absolute_path)} [SCHEMA:{error.validator}] {error.message}"
            )
        _security_check(protocol_root, value)
        records.append((project, value, canonical_sha256(value)))
    return records


def _request_summary(request: dict[str, Any], digest: str, status: str) -> dict[str, Any]:
    slug = request["project"]["slug"]
    return {
        "status": status,
        "request_id": request["request_id"],
        "project_id": f"project/{slug}",
        "request_sha256": digest,
    }


def _collision(root: Path, request: dict[str, Any], digest: str, *, protocol_root: Path | None = None) -> tuple[str, Path | None]:
    project_id = f"project/{request['project']['slug']}"
    target = root / "projects" / request["project"]["slug"]
    existing = _existing_requests(root, protocol_root)
    same_request: tuple[Path, dict[str, Any], str] | None = None
    for project, existing_request, existing_digest in existing:
        if existing_request.get("request_id") == request["request_id"]:
            if existing_digest != digest or project.name != request["project"]["slug"]:
                raise RequestAcceptanceError(
                    f"REQUEST-CONFLICT: request_id {request['request_id']!r} already belongs to {project.relative_to(root)} with different content"
                )
            same_request = (project, existing_request, existing_digest)
    if same_request:
        if not (same_request[0] / RECEIPT_RELATIVE).is_file():
            raise RequestAcceptanceError(f"REQUEST-STATE: accepted request has no receipt: {same_request[0] / RECEIPT_RELATIVE}")
        return "ALREADY_APPLIED", same_request[0]
    if target.exists():
        raise RequestAcceptanceError(f"PROJECT-CONFLICT: project already exists and was not created by this request: {target}")
    return "NEW", None


def _yaml_text(value: Any) -> str:
    return yaml.safe_dump(value, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _creative_intent(request: dict[str, Any]) -> str:
    intent = request["intent"]
    references = request["references"]
    lines = [
        f"# {request['project']['title']} — Creative intent",
        "",
        "## 制作意図",
        "",
        intent["purpose"],
        "",
        "## 現時点の中心命題",
        "",
        intent["creative_question"],
        "",
        "## 利用先",
        "",
        intent["intended_use"],
        "",
        "## 観客に起こしたい体験",
        "",
        intent["audience_experience"] or "未確認。",
        "",
        "## 媒体・素材",
        "",
        ", ".join(intent["medium_materials"]) if intent["medium_materials"] else "未確認。",
        "",
        "## 参照候補",
        "",
    ]
    lines.extend(f"- {item['label']}: {item['uri']} (rights_status={item['rights_status']})" for item in references)
    if not references:
        lines.append("- なし")
    return "\n".join(lines) + "\n"


def _materialize(
    root: Path,
    request: dict[str, Any],
    digest: str,
    accepted_at: str,
    *,
    protocol_root: Path | None = None,
) -> Path:
    slug = request["project"]["slug"]
    project = create_project(
        root,
        slug,
        request["project"]["title"],
        request["project"]["creator_id"],
        created_at=request["requested_at"],
        protocol_root=protocol_root,
    )
    try:
        manifest_path = project / "manifest.yaml"
        manifest = load_yaml(manifest_path)
        manifest["entry_points"]["research_request"] = REQUEST_RELATIVE.as_posix()
        manifest["entry_points"]["research_request_receipt"] = RECEIPT_RELATIVE.as_posix()
        atomic_write_text(manifest_path, _yaml_text(manifest))

        atomic_write_text(project / REQUEST_RELATIVE, _yaml_text(request))
        receipt = {
            "version": 1,
            "request_id": request["request_id"],
            "project_id": f"project/{slug}",
            "request_sha256": digest,
            "accepted_at": accepted_at,
        }
        atomic_write_text(project / RECEIPT_RELATIVE, _yaml_text(receipt))

        constraints_path = project / "00_intake" / "constraints.yaml"
        atomic_write_text(constraints_path, _yaml_text(request["constraints"]))

        intent_path = project / "00_intake" / "creative-intent.md"
        atomic_write_text(intent_path, _creative_intent(request))

        plan_path = project / "01_planning" / "research-plan.yaml"
        plan = load_yaml(plan_path)
        plan["objective"] = request["intent"]["purpose"]
        atomic_write_text(plan_path, _yaml_text(plan))

        from executive_brief import write_executive_brief

        write_executive_brief(root, f"project/{slug}")

        findings = validate_repository(root, f"project/{slug}", protocol_root=protocol_root)
        if findings:
            rendered = "\n".join(finding.render() for finding in findings)
            raise RequestAcceptanceError(f"materialized project failed validation:\n{rendered}")
    except Exception:
        shutil.rmtree(project)
        raise
    return project


def accept_research_request(
    root: Path,
    input_path: Path,
    *,
    dry_run: bool = False,
    apply: bool = False,
    accepted_at: str | None = None,
    protocol_root: Path | None = None,
) -> dict[str, Any]:
    if dry_run == apply:
        raise RequestAcceptanceError("select exactly one of --dry-run or --apply")
    root = root.resolve()
    protocol = (protocol_root or root).resolve()
    request, digest = _validate_request(protocol, input_path.resolve())
    status, existing = _collision(root, request, digest, protocol_root=protocol)
    if status == "ALREADY_APPLIED":
        return _request_summary(request, digest, status)
    if dry_run:
        return _request_summary(request, digest, "DRY_RUN")

    accepted_at = accepted_at or datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    if not TIMESTAMP_PATTERN.fullmatch(accepted_at):
        raise RequestAcceptanceError(f"accepted_at must be an RFC 3339 timestamp: {accepted_at!r}")
    project = _materialize(root, request, digest, accepted_at, protocol_root=protocol)
    summary = _request_summary(request, digest, "APPLIED")
    summary["materialized_path"] = str(project.relative_to(root))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Accept an upstream research request into a new RESEARCH_ONLY project.")
    parser.add_argument("input", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--root", type=Path, help="compatibility alias for --work-root")
    parser.add_argument("--work-root", type=Path, help="temporary work root or external output staging root")
    parser.add_argument("--protocol-root", type=Path, help="read-only protocol checkout containing schemas and policy")
    parser.add_argument("--accepted-at", help="RFC 3339 receipt time; defaults to current Asia/Tokyo time")
    args = parser.parse_args()
    work_root = args.work_root or args.root
    if work_root is None:
        parser.error("one of --work-root or --root is required")
    try:
        summary = accept_research_request(
            work_root,
            args.input,
            dry_run=args.dry_run,
            apply=args.apply,
            accepted_at=args.accepted_at,
            protocol_root=args.protocol_root,
        )
    except (RequestAcceptanceError, OSError) as exc:
        print(f"FAILED: {exc}")
        return 1
    print(stable_json(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
