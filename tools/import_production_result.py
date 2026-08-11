"""Validate and import production-owned result records into a research project."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json
from build_graph import build_graph
from canonical import canonical_sha256, payload_sha256
from handoff_common import HandoffInputError, HandoffSources, load_handoff_sources
from impact import impact_report
from state_machine import load_state_machine
from validate import validate_repository


RESULT_ID_PATTERN = re.compile(r"^PR[0-9]{3,}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMPACT_LEVELS = ("NONE", "MINOR", "MAJOR", "CRITICAL")
IMPACT_RANK = {value: index for index, value in enumerate(IMPACT_LEVELS)}
PROHIBITED_CLASSIFICATION_PATTERN = re.compile(
    r"(?<![A-Z0-9_])(?:PRIVATE_RAW|RESTRICTED)(?![A-Z0-9_])", re.IGNORECASE
)
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'(=:])(?:/(?!/)\S+|[A-Za-z]:[\\/]\S*|~[\\/]\S*|file://\S+)",
    re.IGNORECASE,
)


class ResultImportError(ValueError):
    """Raised when a production result cannot be imported safely."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "production-result",
        field: str | None = None,
        rule: str = "FEEDBACK-IMPORT",
        remediation: str = "Correct the production result or its pinned schema, then retry.",
    ) -> None:
        self.path = path
        self.field = field
        self.rule = rule
        self.remediation = remediation
        super().__init__(message)

    def __str__(self) -> str:
        location = self.path
        if self.field:
            location += f"#{self.field}"
        return f"{location}: [{self.rule}] {self.args[0]}; remediation: {self.remediation}"


def _field_path(parts: Any) -> str:
    field = "$"
    for part in parts:
        field += f"[{part}]" if isinstance(part, int) else f".{part}"
    return field


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def result_sha256(result: dict[str, Any]) -> str:
    """Return the canonical result hash without its self-referential integrity block."""

    return payload_sha256(result)


def _timestamp(value: Any, *, path: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResultImportError("timestamp must be a non-empty RFC 3339 string", path=path, field=field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResultImportError("timestamp is not valid RFC 3339", path=path, field=field) from exc
    if parsed.tzinfo is None:
        raise ResultImportError("timestamp must include a timezone", path=path, field=field)
    return value


def _load_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ResultImportError("production result file was not found", path=str(path), rule="FEEDBACK-INPUT")
    try:
        value = load_json(path) if path.suffix.lower() == ".json" else load_yaml(path)
    except Exception as exc:
        raise ResultImportError(
            f"could not parse production result: {exc}",
            path=str(path),
            rule="FEEDBACK-INPUT",
            remediation="Provide a UTF-8 JSON or YAML object with no duplicate keys.",
        ) from exc
    if not isinstance(value, dict):
        raise ResultImportError(
            "production result must be a mapping/object",
            path=str(path),
            rule="FEEDBACK-INPUT",
            remediation="Wrap the result fields in one top-level object.",
        )
    return value


def _load_policy(root: Path) -> dict[str, Any]:
    path = root / "config" / "handoff-policy.yaml"
    try:
        value = load_yaml(path) or {}
    except Exception as exc:
        raise ResultImportError(f"could not load handoff policy: {exc}", path=str(path), rule="EXTERNAL-SCHEMA") from exc
    if not isinstance(value, dict):
        raise ResultImportError("handoff policy must be a mapping", path=str(path), rule="EXTERNAL-SCHEMA")
    return value


def _schema_path(root: Path, policy: dict[str, Any], override: Path | None) -> Path:
    raw_path = override or policy.get("production_result_schema_path")
    if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
        raise ResultImportError(
            "production result schema path is not configured",
            path="config/handoff-policy.yaml",
            field="production_result_schema_path",
            rule="EXTERNAL-SCHEMA",
            remediation="Configure a commit-pinned production-owned schema snapshot before importing feedback.",
        )
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if root.resolve() not in candidate.parents:
        raise ResultImportError(
            "production result schema must be inside the research root",
            path="config/handoff-policy.yaml",
            field="production_result_schema_path",
            rule="EXTERNAL-SCHEMA",
            remediation="Store only a reviewed immutable schema snapshot below schemas/external/.",
        )
    if not candidate.is_file():
        raise ResultImportError(
            "production-owned result schema snapshot is unavailable",
            path=str(candidate),
            rule="EXTERNAL-SCHEMA",
            remediation="Obtain the production repository schema at its recorded commit; do not invent a consumer schema.",
        )
    return candidate


def _schema_validator(root: Path, policy: dict[str, Any], override: Path | None) -> tuple[Draft202012Validator, dict[str, Any], dict[str, Any]]:
    schema_path = _schema_path(root, policy, override)
    versions = policy.get("production_result_schema_versions")
    if not isinstance(versions, list) or not versions:
        raise ResultImportError(
            "no supported production result schema version is configured",
            path="config/handoff-policy.yaml",
            field="production_result_schema_versions",
            rule="EXTERNAL-SCHEMA",
            remediation="List only versions backed by an immutable production-owned schema snapshot.",
        )
    source = policy.get("production_result_schema_source")
    if not isinstance(source, dict):
        raise ResultImportError(
            "production result schema provenance is not configured",
            path="config/handoff-policy.yaml",
            field="production_result_schema_source",
            rule="EXTERNAL-SCHEMA",
            remediation="Record the source repository, commit, acquired_at, and raw snapshot SHA-256.",
        )
    repository = source.get("repository", source.get("source_repository"))
    commit = source.get("commit", source.get("source_commit"))
    acquired_at = source.get("acquired_at")
    expected_sha = source.get("sha256")
    if not isinstance(repository, str) or not repository.strip():
        raise ResultImportError("schema source repository is required", path="config/handoff-policy.yaml", field="production_result_schema_source.repository", rule="EXTERNAL-SCHEMA")
    if not isinstance(commit, str) or not SHA_PATTERN.fullmatch(commit):
        raise ResultImportError("schema source commit must be a 40-character lowercase SHA", path="config/handoff-policy.yaml", field="production_result_schema_source.commit", rule="EXTERNAL-SCHEMA")
    _timestamp(acquired_at, path="config/handoff-policy.yaml", field="production_result_schema_source.acquired_at")
    if not isinstance(expected_sha, str) or not SHA256_PATTERN.fullmatch(expected_sha):
        raise ResultImportError("schema source SHA-256 is missing or malformed", path="config/handoff-policy.yaml", field="production_result_schema_source.sha256", rule="EXTERNAL-SCHEMA")
    actual_sha = _sha256_bytes(schema_path.read_bytes())
    if actual_sha != expected_sha:
        raise ResultImportError(
            f"schema snapshot SHA-256 {actual_sha!r} does not match configured {expected_sha!r}",
            path=str(schema_path),
            rule="EXTERNAL-SCHEMA",
            remediation="Replace the configuration or snapshot only after checking the production source commit.",
        )
    try:
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        resources: list[tuple[str, Resource]] = []
        for candidate in (schema_path, schema_path.parent / "common.schema.json"):
            if not candidate.is_file():
                continue
            document = load_json(candidate)
            if isinstance(document, dict) and isinstance(document.get("$id"), str):
                resources.append((document["$id"], Resource.from_contents(document)))
        registry = Registry().with_resources(resources)
        validator = Draft202012Validator(schema, registry=registry)
    except Exception as exc:
        raise ResultImportError(
            f"production result schema snapshot is invalid: {exc}",
            path=str(schema_path),
            rule="EXTERNAL-SCHEMA",
            remediation="Restore a valid Draft 2020-12 schema snapshot from the production repository.",
        ) from exc
    return validator, {"path": str(schema_path.relative_to(root)), "version": versions[0], "source_repository": repository, "source_commit": commit, "acquired_at": acquired_at, "sha256": expected_sha}, source


def _validate_schema(result: dict[str, Any], validator: Draft202012Validator, versions: list[Any], input_path: Path) -> None:
    version = result.get("schema_version")
    if version not in versions:
        raise ResultImportError(
            f"unsupported production result schema version {version!r}; available versions: {', '.join(str(item) for item in versions)}",
            path=str(input_path),
            field="schema_version",
            rule="EXTERNAL-SCHEMA",
            remediation="Use a result version listed in the reviewed policy or add its production-owned snapshot.",
        )
    errors = sorted(validator.iter_errors(result), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if errors:
        error = errors[0]
        raise ResultImportError(
            error.message,
            path=str(input_path),
            field=_field_path(error.absolute_path),
            rule=f"EXTERNAL-SCHEMA:{error.validator or 'validation'}",
            remediation="Correct the production result against the commit-pinned production-owned schema.",
        )


def _iter_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in _iter_strings(child, f"{path}.{key}")]
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in _iter_strings(child, f"{path}[{index}]")]
    if isinstance(value, str):
        return [(path, value)]
    return []


def _security_scan(root: Path, result: dict[str, Any], input_path: Path, policy: dict[str, Any]) -> None:
    try:
        access = load_yaml(root / "config" / "access-policy.yaml") or {}
    except Exception as exc:
        raise ResultImportError(f"could not load access policy: {exc}", path="config/access-policy.yaml", rule="FEEDBACK-SECURITY") from exc
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for item in access.get("secret_patterns", []) if isinstance(access, dict) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("pattern"), str):
            continue
        try:
            patterns.append((item["id"], re.compile(item["pattern"])))
        except re.error as exc:
            raise ResultImportError(f"invalid secret pattern {item['id']!r}", path="config/access-policy.yaml", rule="FEEDBACK-SECURITY") from exc
    markers = [str(marker).lower() for marker in policy.get("signed_url_markers", []) if isinstance(marker, str)]
    for field, value in _iter_strings(result):
        if PROHIBITED_CLASSIFICATION_PATTERN.search(value.upper()):
            raise ResultImportError("production result contains a prohibited classification", path=str(input_path), field=field, rule="FEEDBACK-SECURITY", remediation="Remove PRIVATE_RAW or RESTRICTED data and regenerate the result.")
        if ABSOLUTE_PATH_PATTERN.search(value) or "../" in value or "..\\" in value:
            raise ResultImportError("production result contains an absolute or traversal path", path=str(input_path), field=field, rule="FEEDBACK-SECURITY", remediation="Use an approved opaque URI or project-relative ID.")
        lowered = value.lower()
        if "http://" in lowered or "https://" in lowered:
            if any(marker in lowered for marker in markers):
                raise ResultImportError("production result contains a signed or credential-bearing URL", path=str(input_path), field=field, rule="FEEDBACK-SECURITY", remediation="Replace it with a stable approved URI and content hash.")
        for pattern_id, regex in patterns:
            if regex.search(value):
                raise ResultImportError(f"production result matches secret pattern {pattern_id}", path=str(input_path), field=field, rule="FEEDBACK-SECURITY", remediation="Remove the secret and rotate it in its source system.")


def _validate_handoff_reference(root: Path, target: str, result: dict[str, Any], sources: HandoffSources, input_path: Path) -> None:
    accepted = result.get("accepted_handoff")
    if not isinstance(accepted, dict):
        raise ResultImportError("accepted_handoff must be an object", path=str(input_path), field="accepted_handoff", rule="FEEDBACK-REFERENCE")
    handoff = sources.handoff
    if handoff.get("status") not in {"READY", "ACCEPTED"}:
        raise ResultImportError("research handoff is not receivable", path=f"{target}/05_production/production-handoff.yaml", field="status", rule="FEEDBACK-REFERENCE", remediation="Import only against a READY or ACCEPTED handoff.")
    expected_hash = handoff.get("integrity", {}).get("content_sha256") if isinstance(handoff.get("integrity"), dict) else None
    checks = {
        "id": handoff.get("handoff_id"),
        "content_sha256": expected_hash,
        "research_project_id": sources.project_id,
        "research_commit": handoff.get("research_commit"),
    }
    for field, expected in checks.items():
        if accepted.get(field) != expected:
            raise ResultImportError(
                f"accepted_handoff.{field} does not match the canonical research handoff",
                path=str(input_path),
                field=f"accepted_handoff.{field}",
                rule="FEEDBACK-REFERENCE",
                remediation="Return the result against the exact accepted handoff ID, revision content hash, project, and source commit.",
            )
    production_commit = result.get("production_commit")
    if not isinstance(production_commit, str) or not SHA_PATTERN.fullmatch(production_commit):
        raise ResultImportError("production_commit must be a 40-character lowercase SHA", path=str(input_path), field="production_commit", rule="FEEDBACK-PROVENANCE")


def _record_level(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    candidates = [value.get("impact_level")]
    impact = value.get("impact")
    if isinstance(impact, dict):
        candidates.append(impact.get("level"))
    candidates.extend(value.get(key) for key in ("severity", "level"))
    levels = [candidate for candidate in candidates if isinstance(candidate, str) and candidate in IMPACT_RANK]
    return max(levels, key=IMPACT_RANK.get) if levels else None


def _impact_level(result: dict[str, Any]) -> str:
    levels: list[str] = []
    explicit = _record_level(result)
    if explicit:
        levels.append(explicit)
    if result.get("open_gaps"):
        levels.append("MINOR")
    if result.get("deviations") or result.get("incidents") or result.get("research_change_requests"):
        levels.append("MAJOR")
    for test in result.get("test_results", []) if isinstance(result.get("test_results"), list) else []:
        if isinstance(test, dict):
            if test.get("result") in {"FAIL", "BLOCKED"}:
                levels.append("MAJOR")
            elif test.get("result") == "EXTERNAL_VALIDATION_REQUIRED":
                levels.append("MINOR")
            if _record_level(test):
                levels.append(_record_level(test) or "NONE")
    for collection_name in ("deviations", "incidents", "research_change_requests", "open_gaps"):
        for record in result.get(collection_name, []) if isinstance(result.get(collection_name), list) else []:
            level = _record_level(record)
            if level:
                levels.append(level)
    return max(levels, key=IMPACT_RANK.get) if levels else "NONE"


def _next_id(records: list[dict[str, Any]], prefix: str) -> int:
    values = [int(match.group(1)) for record in records if isinstance(record.get("id"), str) and (match := re.fullmatch(rf"{prefix}([0-9]+)", record["id"]))]
    return max(values, default=0) + 1


def _candidate_records(result: dict[str, Any], sources: HandoffSources) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = list(sources.evidence)
    evidence_by_location = {
        record.get("source_location"): record
        for record in evidence
        if isinstance(record, dict) and isinstance(record.get("source_location"), str)
    }
    requirement_ids = {record.get("id") for record in sources.requirements if isinstance(record, dict)}
    next_number = _next_id(evidence, "EV")
    candidates: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    result_id = result["result_id"]
    generated_at = result["generated_at"]
    production_project_id = result["production_project_id"]
    for collection_name, source_type in (("observations", "production_result_observation"), ("test_results", "production_test_result")):
        records = result.get(collection_name, [])
        if not isinstance(records, list):
            raise ResultImportError(f"{collection_name} must be a list", field=collection_name, rule="FEEDBACK-INPUT")
        for index, source in enumerate(records):
            if not isinstance(source, dict):
                raise ResultImportError(f"{collection_name}[{index}] must be an object", field=f"{collection_name}[{index}]", rule="FEEDBACK-INPUT")
            source_id = source.get("id") if collection_name == "observations" else source.get("acceptance_test_id")
            if not isinstance(source_id, str) or not source_id:
                raise ResultImportError("production observation/test needs a stable source ID", field=f"{collection_name}[{index}].id", rule="FEEDBACK-REFERENCE")
            statement = source.get("statement") if collection_name == "observations" else f"Acceptance test {source_id} result: {source.get('result')}"
            if collection_name == "test_results" and source.get("conditions"):
                statement += f" Conditions: {source['conditions']}"
            source_location = f"urn:agentic-art-production:result:{result_id}:{collection_name}:{source_id}"
            content_hash = canonical_sha256(source)
            existing_candidate = evidence_by_location.get(source_location)
            if existing_candidate is not None:
                if existing_candidate.get("content_hash") != content_hash:
                    raise ResultImportError(
                        f"source record {source_id!r} already has different evidence content",
                        field=f"{collection_name}[{index}]",
                        rule="FEEDBACK-IDEMPOTENCY",
                        remediation="Use a new production result ID for changed source content.",
                    )
                candidate_id = existing_candidate["id"]
            else:
                candidate_id = f"EV{next_number:03d}"
                next_number += 1
            candidate = {
                "id": candidate_id,
                "source_type": source_type,
                "source_location": source_location,
                "created_at": generated_at,
                "acquired_at": generated_at,
                "content_hash": content_hash,
                "rights_status": "production-result-derived",
                "sensitivity": "PROJECT_INTERNAL",
                "redistribution": "conditional",
                "related_projects": [sources.project_id],
                "related_questions": [],
                "extraction_status": "unprocessed",
                "direct_observation": True,
                "observed_by": production_project_id,
            }
            candidates.append(candidate)
            related_requirements = source.get("related_requirement_ids", []) if collection_name == "observations" else []
            if not isinstance(related_requirements, list) or not all(isinstance(item, str) for item in related_requirements):
                raise ResultImportError("related_requirement_ids must be a list of strings", field=f"{collection_name}[{index}].related_requirement_ids", rule="FEEDBACK-REFERENCE")
            unknown_requirements = sorted(set(related_requirements) - requirement_ids)
            if unknown_requirements:
                raise ResultImportError(
                    f"observation references unknown requirements: {', '.join(unknown_requirements)}",
                    field=f"{collection_name}[{index}].related_requirement_ids",
                    rule="FEEDBACK-REFERENCE",
                    remediation="Reference only requirements declared by the accepted research handoff.",
                )
            if collection_name == "test_results":
                related_requirements = []
            provenance.append({"candidate_id": candidate_id, "kind": collection_name, "source_id": source_id, "related_requirement_ids": sorted(set(related_requirements))})
            if existing_candidate is None:
                evidence_by_location[source_location] = candidate
                evidence.append(candidate)
    return candidates, provenance, evidence


def _change_records(result: dict[str, Any], sources: HandoffSources, impact_level: str) -> tuple[list[dict[str, Any]], list[str]]:
    document_path = sources.project / "06_governance" / "production-change-requests.yaml"
    document = load_yaml(document_path) or {}
    if not isinstance(document, dict) or not isinstance(document.get("change_requests", []), list):
        raise ResultImportError("change_requests must be a list", path=str(document_path), field="change_requests", rule="FEEDBACK-GOVERNANCE")
    existing = [record for record in document["change_requests"] if isinstance(record, dict)]
    next_number = _next_id(existing, "CR")
    records: list[dict[str, Any]] = []
    for collection_name, kind in (("deviations", "DEVIATION"), ("incidents", "INCIDENT"), ("research_change_requests", "RESEARCH_CHANGE_REQUEST")):
        values = result.get(collection_name, [])
        if not isinstance(values, list):
            raise ResultImportError(f"{collection_name} must be a list", field=collection_name, rule="FEEDBACK-INPUT")
        for index, source in enumerate(values):
            if not isinstance(source, dict):
                raise ResultImportError(f"{collection_name}[{index}] must be an object", field=f"{collection_name}[{index}]", rule="FEEDBACK-GOVERNANCE")
            source_id = source.get("id") or source.get("request_id") or f"{kind}-{index + 1:03d}"
            summary = next((source.get(key) for key in ("requested_change", "statement", "reason", "description") if isinstance(source.get(key), str) and source[key].strip()), None)
            if not summary:
                raise ResultImportError("governance record needs a concise statement or reason", field=f"{collection_name}[{index}]", rule="FEEDBACK-GOVERNANCE")
            existing_record = next(
                (
                    record
                    for record in existing
                    if record.get("source_result_id") == result["result_id"]
                    and record.get("source_record_id") == str(source_id)
                    and record.get("kind") == kind
                ),
                None,
            )
            if existing_record is not None:
                records.append(existing_record)
                continue
            local_level = _record_level(source) or ("CRITICAL" if kind == "INCIDENT" and impact_level == "CRITICAL" else impact_level)
            record_id = f"CR{next_number:03d}"
            next_number += 1
            record = {
                "id": record_id,
                "kind": kind,
                "source_result_id": result["result_id"],
                "source_record_id": str(source_id),
                "statement": " ".join(summary.split())[:500],
                "impact_level": local_level,
                "research_review_required": local_level in {"MAJOR", "CRITICAL"},
                "requires_human_approval": local_level == "CRITICAL",
                "status": "PENDING_HUMAN_APPROVAL" if local_level == "CRITICAL" else "PROPOSED",
            }
            affected = source.get("affected_ids")
            if isinstance(affected, list) and all(isinstance(item, str) for item in affected):
                record["affected_ids"] = sorted(set(affected))
            records.append(record)
    return records, [record["id"] for record in records]


def _impact_preview(root: Path, target: str, provenance: list[dict[str, Any]]) -> list[dict[str, Any]]:
    graph = build_graph(root)
    nodes: set[str] = set()
    for item in provenance:
        nodes.update(item.get("related_requirement_ids", []))
    result: list[dict[str, Any]] = []
    for node in sorted(nodes):
        result.append({"node": node, "report": impact_report(graph, f"{target}::{node}")})
    return result


def _existing_result(records: list[dict[str, Any]], result: dict[str, Any], input_path: Path) -> tuple[bool, str | None]:
    result_id = result.get("result_id")
    for existing in records:
        if existing.get("result_id") != result_id:
            continue
        existing_hash = result_sha256(existing)
        incoming_hash = result_sha256(result)
        if existing_hash == incoming_hash:
            return True, existing_hash
        raise ResultImportError(
            f"result ID {result_id!r} already exists with a different payload hash",
            path=str(input_path),
            field="result_id",
            rule="FEEDBACK-IDEMPOTENCY",
            remediation="Use a new result ID for changed content; never overwrite an imported result.",
        )
    return False, None


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> str:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if current and not current.endswith("\n"):
        current += "\n"
    return current + "".join(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for record in records)


def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, content.decode("utf-8"))


def _summary(
    *,
    status: str,
    result: dict[str, Any],
    schema_info: dict[str, Any],
    impact_level: str,
    candidate_ids: list[str],
    change_request_ids: list[str],
    impact: list[dict[str, Any]],
    reopened: bool,
    human_approval_required: bool,
) -> dict[str, Any]:
    return {
        "status": status,
        "result_id": result["result_id"],
        "schema_version": result["schema_version"],
        "result_sha256": result_sha256(result),
        "schema_snapshot": schema_info,
        "impact_level": impact_level,
        "evidence_candidate_ids": candidate_ids,
        "change_request_ids": change_request_ids,
        "research_reopened": reopened,
        "human_approval_required": human_approval_required,
        "impact": impact,
    }


def import_production_result(
    root: Path,
    input_path: Path,
    *,
    dry_run: bool = False,
    apply: bool = False,
    project_target: str | None = None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    if dry_run == apply:
        raise ResultImportError("choose exactly one of dry_run or apply", rule="FEEDBACK-MODE", remediation="Use --dry-run for read-only inspection or --apply for the guarded write path.")
    root = root.resolve()
    input_path = input_path.resolve()
    policy = _load_policy(root)
    validator, schema_info, _ = _schema_validator(root, policy, schema_path)
    result = _load_result(input_path)
    versions = policy.get("production_result_schema_versions", [])
    _validate_schema(result, validator, versions, input_path)
    _security_scan(root, result, input_path, policy)
    result_id = result.get("result_id")
    if not isinstance(result_id, str) or not RESULT_ID_PATTERN.fullmatch(result_id):
        raise ResultImportError("result_id must match PR followed by at least three digits", path=str(input_path), field="result_id", rule="FEEDBACK-REFERENCE")
    generated_at = _timestamp(result.get("generated_at"), path=str(input_path), field="generated_at")
    result["generated_at"] = generated_at
    accepted = result.get("accepted_handoff") if isinstance(result.get("accepted_handoff"), dict) else {}
    target = project_target or accepted.get("research_project_id")
    if not isinstance(target, str) or not target.startswith("project/") or target.count("/") != 1:
        raise ResultImportError("research project target cannot be derived from accepted_handoff", path=str(input_path), field="accepted_handoff.research_project_id", rule="FEEDBACK-REFERENCE", remediation="Use a project/<slug> research project ID.")
    findings = validate_repository(root, target)
    if findings:
        rendered = "\n".join(finding.render() for finding in findings)
        raise ResultImportError(f"research project is not valid before import:\n{rendered}", path=target, rule="FEEDBACK-RESEARCH-VALIDATION", remediation="Resolve the existing project findings before importing production feedback.")
    try:
        sources = load_handoff_sources(root, target)
    except HandoffInputError:
        raise
    _validate_handoff_reference(root, target, result, sources, input_path)
    expected_hash = result_sha256(result)
    integrity = result.get("integrity") if isinstance(result.get("integrity"), dict) else {}
    if integrity.get("content_sha256") != expected_hash:
        raise ResultImportError("result integrity hash does not match the canonical payload", path=str(input_path), field="integrity.content_sha256", rule="FEEDBACK-HASH", remediation="Recalculate the result hash with the production-owned canonical serializer.")
    feedback_path = sources.project / "07_runtime" / "production-feedback-imports.jsonl"
    existing_feedback = read_jsonl(feedback_path)
    same_result_recorded, _ = _existing_result(existing_feedback, result, input_path)
    impact_level = _impact_level(result)
    prior_evidence = list(sources.evidence)
    candidates, provenance, all_evidence = _candidate_records(result, sources)
    change_records, change_ids = _change_records(result, sources, impact_level)
    impact = _impact_preview(root, target, provenance)
    human_approval_required = impact_level == "CRITICAL"
    audit_events = read_jsonl(sources.project / "07_runtime" / "run-log.jsonl")
    existing_audit = next(
        (event for event in audit_events if event.get("event_type") == "PRODUCTION_FEEDBACK_IMPORTED" and event.get("result_id") == result_id),
        None,
    )
    existing_evidence_ids = {record.get("id") for record in all_evidence}
    existing_change_document = load_yaml(sources.project / "06_governance" / "production-change-requests.yaml") or {}
    existing_change_ids = {
        record.get("id")
        for record in existing_change_document.get("change_requests", [])
        if isinstance(record, dict)
    }
    complete_effect = bool(
        same_result_recorded
        and existing_audit
        and set(existing_audit.get("evidence_candidate_ids", [])) <= existing_evidence_ids
        and set(existing_audit.get("change_request_ids", [])) <= existing_change_ids
    )
    if complete_effect:
        return _summary(
            status="ALREADY_APPLIED",
            result=result,
            schema_info=schema_info,
            impact_level=impact_level,
            candidate_ids=list(existing_audit.get("evidence_candidate_ids", [])),
            change_request_ids=list(existing_audit.get("change_request_ids", [])),
            impact=impact,
            reopened=bool(existing_audit.get("research_reopened")),
            human_approval_required=bool(existing_audit.get("human_approval_required")),
        )
    if dry_run:
        return _summary(
            status="DRY_RUN",
            result=result,
            schema_info=schema_info,
            impact_level=impact_level,
            candidate_ids=[record["id"] for record in candidates],
            change_request_ids=change_ids,
            impact=impact,
            reopened=impact_level in {"MAJOR", "CRITICAL"},
            human_approval_required=human_approval_required,
        )

    project = sources.project
    manifest_path = project / "manifest.yaml"
    state_path = project / "07_runtime" / "research-state.json"
    change_path = project / "06_governance" / "production-change-requests.yaml"
    evidence_path = project / "02_evidence" / "evidence-ledger.jsonl"
    run_log_path = project / "07_runtime" / "run-log.jsonl"
    paths = [manifest_path, state_path, change_path, evidence_path, feedback_path, run_log_path]
    before = _snapshot(paths)
    manifest = load_yaml(manifest_path) or {}
    state = load_json(state_path)
    run_events = read_jsonl(run_log_path)
    import_event_id = f"FEEDBACK-{result_id}-IMPORT"
    import_event_exists = any(event.get("event_id", event.get("id")) == import_event_id for event in run_events)
    transition_event: dict[str, Any] | None = None
    current_status = (manifest.get("project") or {}).get("status") if isinstance(manifest, dict) else None
    if impact_level == "MAJOR" and current_status != "ANALYZING":
        machine = load_state_machine(root)
        if not machine.is_allowed(current_status, "ANALYZING"):
            raise ResultImportError(f"MAJOR feedback cannot reopen project from {current_status!r}", path=str(manifest_path), field="project.status", rule="FEEDBACK-REOPEN", remediation="Record a human-reviewed transition or resolve the project lifecycle before applying this result.")
        transition_event = {"event_id": f"FEEDBACK-{result_id}-REOPEN", "event_type": "STATE_TRANSITION", "from_status": current_status, "to_status": "ANALYZING"}
        manifest["project"]["status"] = "ANALYZING"
        manifest["project"]["updated_at"] = generated_at
        state["status"] = "ANALYZING"
        state["updated_at"] = generated_at
        state["last_event_id"] = transition_event["event_id"]
    audit_event = {
        "event_id": import_event_id,
        "event_type": "PRODUCTION_FEEDBACK_IMPORTED",
        "occurred_at": generated_at,
        "effect_key": f"production-result:{result_id}:{expected_hash}",
        "result_id": result_id,
        "result_sha256": expected_hash,
        "schema_version": result["schema_version"],
        "production_project_id": result["production_project_id"],
        "accepted_handoff_id": result["accepted_handoff"]["id"],
        "impact_level": impact_level,
        "evidence_candidate_ids": [record["id"] for record in candidates],
        "change_request_ids": change_ids,
        "research_reopened": transition_event is not None,
        "reopen_event_id": transition_event["event_id"] if transition_event else None,
        "human_approval_required": human_approval_required,
        "human_approval_status": "PENDING" if human_approval_required else "NOT_REQUIRED",
    }
    try:
        if transition_event:
            atomic_write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
            atomic_write_text(state_path, stable_json(state))
        existing_evidence_locations = {
            record.get("source_location")
            for record in prior_evidence
            if isinstance(record, dict)
        }
        new_candidates = [record for record in candidates if record.get("source_location") not in existing_evidence_locations]
        if new_candidates:
            atomic_write_text(evidence_path, _append_jsonl(evidence_path, new_candidates))
        existing_change_keys = {
            (record.get("kind"), record.get("source_result_id"), record.get("source_record_id"))
            for record in existing_change_document.get("change_requests", [])
            if isinstance(record, dict)
        }
        new_change_records = [
            record
            for record in change_records
            if (record.get("kind"), record.get("source_result_id"), record.get("source_record_id")) not in existing_change_keys
        ]
        if new_change_records:
            change_document = load_yaml(change_path) or {}
            change_document["change_requests"] = list(change_document.get("change_requests", [])) + new_change_records
            atomic_write_text(change_path, yaml.safe_dump(change_document, sort_keys=False, allow_unicode=True))
        if not same_result_recorded:
            atomic_write_text(feedback_path, _append_jsonl(feedback_path, [result]))
        events_to_append = []
        if transition_event and not any(event.get("event_id") == transition_event["event_id"] for event in run_events):
            events_to_append.append(transition_event)
        if not import_event_exists:
            events_to_append.append(audit_event)
        if events_to_append:
            atomic_write_text(run_log_path, _append_jsonl(run_log_path, events_to_append))
        findings = validate_repository(root, target)
        if findings:
            rendered = "\n".join(finding.render() for finding in findings)
            raise ResultImportError(f"applied production result failed research validation:\n{rendered}", path=target, rule="FEEDBACK-VALIDATION", remediation="Restore the previous project state, correct the result mapping, and retry.")
    except Exception:
        _restore(before)
        raise
    return _summary(
        status="APPLIED",
        result=result,
        schema_info=schema_info,
        impact_level=impact_level,
        candidate_ids=[record["id"] for record in candidates],
        change_request_ids=change_ids,
        impact=impact,
        reopened=transition_event is not None,
        human_approval_required=human_approval_required,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and import a production-owned result without copying raw assets.")
    parser.add_argument("input", type=Path, help="production result JSON or YAML")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="validate and preview impact without writing tracked files")
    mode.add_argument("--apply", action="store_true", help="apply the validated result exactly once")
    parser.add_argument("--project", help="override the project/<slug> target derived from accepted_handoff")
    parser.add_argument("--schema", type=Path, help="development-only schema path override; provenance policy is still required")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        summary = import_production_result(
            args.root.resolve(),
            args.input,
            dry_run=args.dry_run,
            apply=args.apply,
            project_target=args.project,
            schema_path=args.schema,
        )
    except (HandoffInputError, ResultImportError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(stable_json(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
