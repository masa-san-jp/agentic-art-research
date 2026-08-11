#!/usr/bin/env python3
"""Fail-closed adapter for consent-approved private derived evidence.

The adapter accepts an already-derived, repository-safe descriptor.  It never
reads a private source location and never accepts raw content.  Its output is
the only shape that callers should persist in a project package.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from _common import atomic_write_text, stable_json


ADAPTER_SCHEMA = "urn:agentic-art-research:private-evidence-adapter:v1"
OPAQUE_SCHEMES = {"gdrive", "drive", "vault", "private", "opaque"}
APPROVAL_SCHEMES = OPAQUE_SCHEMES | {"consent"}
SIGNAL_CATEGORIES = {
    "seeks",
    "protects",
    "avoids",
    "reacts_against",
    "drawn_toward",
    "influenced_by",
    "tensions",
    "recurring_patterns",
    "emotional_material",
}
CERTAINTIES = {"unknown", "low", "medium", "high"}
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^EV[0-9]{3,}$")
SIGNAL_ID_RE = re.compile(r"^SIG[0-9]{3,}$")
SUBJECT_RE = re.compile(r"^subject/[a-z0-9]+(?:-[a-z0-9]+)*$")
PROJECT_RE = re.compile(r"^project/[a-z0-9]+(?:-[a-z0-9]+)*$")
QUESTION_RE = re.compile(r"^Q[0-9]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
OPAQUE_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*(?:/[A-Za-z0-9][A-Za-z0-9._~-]*)*$")


class AdapterError(ValueError):
    """Raised when input is not safe to cross the repository boundary."""


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdapterError(f"{field} must be an object")
    return value


def _strict_keys(value: dict[str, Any], required: set[str], allowed: set[str], field: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise AdapterError(f"{field} is missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise AdapterError(f"{field} contains unsupported field(s): {', '.join(unknown)}")


def _string(value: Any, field: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise AdapterError(f"{field} must be a non-empty string")
    if pattern is not None and not pattern.fullmatch(value):
        raise AdapterError(f"{field} has an invalid format")
    return value


def _timestamp(value: Any, field: str) -> str:
    return _string(value, field, pattern=TIMESTAMP_RE)


def _opaque_uri(value: Any, field: str, schemes: set[str]) -> str:
    uri = _string(value, field)
    parsed = urlsplit(uri)
    if parsed.scheme not in schemes or not parsed.netloc or parsed.username or parsed.password:
        raise AdapterError(f"{field} must be an opaque URI with an approved scheme")
    if parsed.query or parsed.fragment or parsed.port is not None:
        raise AdapterError(f"{field} must not contain query, fragment, or port data")
    if not OPAQUE_PART_RE.fullmatch(parsed.netloc + parsed.path):
        raise AdapterError(f"{field} must contain only opaque identifier segments")
    return uri


def _unique_strings(value: Any, field: str, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AdapterError(f"{field} must be a list of strings")
    if len(value) != len(set(value)):
        raise AdapterError(f"{field} must not contain duplicates")
    for item in value:
        if not pattern.fullmatch(item):
            raise AdapterError(f"{field} contains an invalid identifier")
    return sorted(value)


def _validate_signal_source(value: Any) -> dict[str, str]:
    source = _mapping(value, "signal_source")
    allowed = {"schema", "source_repository", "source_commit"}
    _strict_keys(source, allowed, allowed, "signal_source")
    schema = _string(source["schema"], "signal_source.schema")
    repository = _string(source["source_repository"], "signal_source.source_repository")
    if "/" not in repository or any(char.isspace() for char in repository):
        raise AdapterError("signal_source.source_repository must be owner/repository metadata")
    commit = _string(source["source_commit"], "signal_source.source_commit", pattern=COMMIT_RE)
    return {"schema": schema, "source_repository": repository, "source_commit": commit}


def _validate_approval(value: Any) -> dict[str, str]:
    approval = _mapping(value, "approval")
    allowed = {"purpose", "operation", "approval_ref", "approved_at"}
    _strict_keys(approval, allowed, allowed, "approval")
    purpose = _string(approval["purpose"], "approval.purpose", pattern=SLUG_RE)
    operation = _string(approval["operation"], "approval.operation", pattern=SLUG_RE)
    approval_ref = _opaque_uri(approval["approval_ref"], "approval.approval_ref", APPROVAL_SCHEMES)
    approved_at = _timestamp(approval["approved_at"], "approval.approved_at")
    return {
        "purpose": purpose,
        "operation": operation,
        "approval_ref": approval_ref,
        "approved_at": approved_at,
    }


def _validate_signals(value: Any, evidence_id: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise AdapterError("approved_derived_signals must be a non-empty list")
    if len(value) > 32:
        raise AdapterError("approved_derived_signals must contain at most 32 signals")
    signals: list[dict[str, Any]] = []
    signal_ids: set[str] = set()
    for index, raw_signal in enumerate(value):
        field = f"approved_derived_signals[{index}]"
        signal = _mapping(raw_signal, field)
        allowed = {"signal_id", "category", "code", "certainty", "evidence_refs"}
        _strict_keys(signal, allowed, allowed, field)
        signal_id = _string(signal["signal_id"], f"{field}.signal_id", pattern=SIGNAL_ID_RE)
        if signal_id in signal_ids:
            raise AdapterError(f"{field}.signal_id is duplicated")
        signal_ids.add(signal_id)
        category = _string(signal["category"], f"{field}.category")
        if category not in SIGNAL_CATEGORIES:
            raise AdapterError(f"{field}.category is not an approved derived-signal category")
        code = _string(signal["code"], f"{field}.code", pattern=SLUG_RE)
        certainty = _string(signal["certainty"], f"{field}.certainty")
        if certainty not in CERTAINTIES:
            raise AdapterError(f"{field}.certainty is not an approved certainty")
        evidence_refs = _unique_strings(signal["evidence_refs"], f"{field}.evidence_refs", ID_RE)
        if evidence_id not in evidence_refs:
            raise AdapterError(f"{field}.evidence_refs must include {evidence_id}")
        signals.append(
            {
                "signal_id": signal_id,
                "category": category,
                "code": code,
                "certainty": certainty,
                "evidence_refs": evidence_refs,
            }
        )
    return sorted(signals, key=lambda item: item["signal_id"])


def adapt_private_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Return the repository-safe representation of one private source.

    This function intentionally requires ``PRIVATE_DERIVED`` input.  It does
    not convert ``PRIVATE_RAW`` into a permitted class; callers must perform
    consent and human approval before invoking this boundary.
    """

    record = _mapping(record, "record")
    required = {
        "id",
        "source_type",
        "source_location",
        "created_at",
        "acquired_at",
        "content_hash",
        "rights_status",
        "sensitivity",
        "redistribution",
        "related_projects",
        "related_questions",
        "extraction_status",
        "direct_observation",
        "approval",
        "signal_source",
        "subject",
        "approved_derived_signals",
    }
    _strict_keys(record, required, required, "record")

    evidence_id = _string(record["id"], "id", pattern=ID_RE)
    source_type = _string(record["source_type"], "source_type", pattern=SLUG_RE)
    source_location = _opaque_uri(record["source_location"], "source_location", OPAQUE_SCHEMES)
    created_at = record["created_at"]
    if created_at != "unknown" and created_at is not None:
        created_at = _timestamp(created_at, "created_at")
    acquired_at = _timestamp(record["acquired_at"], "acquired_at")
    content_hash = _string(record["content_hash"], "content_hash", pattern=SHA_RE)
    rights_status = _string(record["rights_status"], "rights_status", pattern=SLUG_RE)
    if record["sensitivity"] != "PRIVATE_DERIVED":
        raise AdapterError("sensitivity must be PRIVATE_DERIVED; raw and restricted sources are not exportable")
    if record["redistribution"] not in {"prohibited", "conditional"}:
        raise AdapterError("redistribution must be prohibited or conditional for private derived evidence")
    related_projects = _unique_strings(record["related_projects"], "related_projects", PROJECT_RE)
    related_questions = _unique_strings(record["related_questions"], "related_questions", QUESTION_RE)
    if record["extraction_status"] != "processed":
        raise AdapterError("extraction_status must be processed")
    if record["direct_observation"] is not False:
        raise AdapterError("direct_observation must be false for derived private evidence")
    subject = _string(record["subject"], "subject", pattern=SUBJECT_RE)
    approval = _validate_approval(record["approval"])
    signal_source = _validate_signal_source(record["signal_source"])
    signals = _validate_signals(record["approved_derived_signals"], evidence_id)

    return {
        "schema": ADAPTER_SCHEMA,
        "subject": subject,
        "evidence": {
            "id": evidence_id,
            "source_type": source_type,
            "source_location": source_location,
            "created_at": created_at,
            "acquired_at": acquired_at,
            "content_hash": content_hash,
            "rights_status": rights_status,
            "sensitivity": "PRIVATE_DERIVED",
            "redistribution": record["redistribution"],
            "related_projects": related_projects,
            "related_questions": related_questions,
            "extraction_status": "processed",
            "direct_observation": False,
            "observed_by": "private-evidence-adapter",
        },
        "approval": approval,
        "signal_source": signal_source,
        "approved_derived_signals": signals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and normalize a private derived evidence descriptor.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        record = json.loads(args.input.read_text(encoding="utf-8"))
        content = stable_json(adapt_private_evidence(record))
    except (OSError, json.JSONDecodeError, AdapterError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
