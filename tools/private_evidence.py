from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from _common import ROOT, atomic_write_text, load_yaml, read_jsonl, stable_json


HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ID_RE = re.compile(r"^(EV|CL)([0-9]{3,})$")
PROJECT_RE = re.compile(r"^project/[a-z0-9]+(?:-[a-z0-9]+)*$")
QUESTION_RE = re.compile(r"^Q[0-9]{3,}$")
class PrivateEvidenceAdapterError(ValueError):
    """Raised when a private-source input cannot satisfy the no-raw contract."""


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PrivateEvidenceAdapterError(f"{field} must be a non-empty string")
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise PrivateEvidenceAdapterError(f"{field} must be an RFC 3339 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PrivateEvidenceAdapterError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PrivateEvidenceAdapterError(f"{field} must include a timezone")
    return value


def _date_or_null(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PrivateEvidenceAdapterError(f"{field} must be an ISO date or null")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise PrivateEvidenceAdapterError(f"{field} must be an ISO date or null") from exc
    return value


def _project_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not PROJECT_RE.fullmatch(value):
        raise PrivateEvidenceAdapterError(f"{field} must be a canonical project ID")
    return value


def _question_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not QUESTION_RE.fullmatch(value):
        raise PrivateEvidenceAdapterError(f"{field} must be a canonical question ID")
    return value


class PrivateEvidenceAdapter:
    """Accept only private-source metadata and compact approved derived signals.

    The adapter never opens or copies a source URI. It constructs repository
    records from a strict whitelist, so unapproved input fields cannot leak
    into evidence or claim JSONL.
    """

    def __init__(self, repository_root: Path = ROOT) -> None:
        self.repository_root = repository_root.resolve()
        config_path = self.repository_root / "config" / "private-evidence.yaml"
        config = load_yaml(config_path) or {}
        if not isinstance(config, dict):
            raise PrivateEvidenceAdapterError("config/private-evidence.yaml must be a mapping")
        self.config = config
        self.allowed_schemes = self._string_set("allowed_opaque_uri_schemes")
        self.allowed_sensitivities = self._string_set("allowed_sensitivities")
        self.allowed_epistemic_statuses = self._string_set("allowed_derived_epistemic_statuses")
        self.forbidden_input_fields = self._string_set("forbidden_input_fields")
        self.required_metadata_fields = self._string_set("metadata_required_fields")
        self.optional_metadata_fields = self._string_set("metadata_optional_fields")
        self.signal_fields = self._string_set("derived_signal_fields")
        self.metadata_fields = self.required_metadata_fields | self.optional_metadata_fields
        if self.required_metadata_fields & self.optional_metadata_fields:
            raise PrivateEvidenceAdapterError("private evidence metadata required and optional fields overlap")
        if self.allowed_sensitivities != {"PRIVATE_DERIVED"}:
            raise PrivateEvidenceAdapterError("private evidence policy must allow only PRIVATE_DERIVED")
        if self.config.get("derived_redistribution") != "prohibited":
            raise PrivateEvidenceAdapterError("private evidence policy must prohibit redistribution")
        if self.config.get("derived_extraction_status") != "processed":
            raise PrivateEvidenceAdapterError("private evidence policy must require processed extraction")
        if self.config.get("derived_claim_type") != "PREFERENCE_SIGNAL":
            raise PrivateEvidenceAdapterError("private evidence policy must use PREFERENCE_SIGNAL")

    def _string_set(self, key: str) -> set[str]:
        values = self.config.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
            raise PrivateEvidenceAdapterError(f"config/private-evidence.yaml: {key} must be a non-empty string list")
        if len(set(values)) != len(values):
            raise PrivateEvidenceAdapterError(f"config/private-evidence.yaml: {key} contains duplicates")
        return set(values)

    def _validate_source_location(self, value: Any) -> str:
        source_location = _non_empty_string(value, "source_location")
        if any(character.isspace() for character in source_location):
            raise PrivateEvidenceAdapterError("source_location must not contain whitespace")
        parsed = urlsplit(source_location)
        if parsed.scheme not in self.allowed_schemes:
            raise PrivateEvidenceAdapterError("source_location uses a non-approved opaque URI scheme")
        if self.config.get("require_uri_authority") and not parsed.netloc:
            raise PrivateEvidenceAdapterError("source_location must contain an opaque URI authority")
        if parsed.username is not None or parsed.password is not None:
            raise PrivateEvidenceAdapterError("source_location must not contain credentials")
        if any(part == ".." for part in parsed.path.split("/")):
            raise PrivateEvidenceAdapterError("source_location must not contain path traversal")
        return source_location

    def _validate_metadata(self, metadata: Any) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise PrivateEvidenceAdapterError("metadata must be an object containing metadata only")
        forbidden = sorted(set(metadata) & self.forbidden_input_fields)
        if forbidden:
            raise PrivateEvidenceAdapterError(f"metadata contains forbidden raw field: {forbidden[0]}")
        unknown = sorted(set(metadata) - self.metadata_fields)
        if unknown:
            raise PrivateEvidenceAdapterError(f"metadata contains unsupported field: {unknown[0]}")
        missing = sorted(self.required_metadata_fields - set(metadata))
        if missing:
            raise PrivateEvidenceAdapterError(f"metadata is missing required field: {missing[0]}")

        source_type = _non_empty_string(metadata["source_type"], "source_type")
        source_location = self._validate_source_location(metadata["source_location"])
        content_hash = _non_empty_string(metadata["content_hash"], "content_hash")
        if not HASH_RE.fullmatch(content_hash):
            raise PrivateEvidenceAdapterError("content_hash must be a lowercase sha256 digest")
        rights_status = _non_empty_string(metadata["rights_status"], "rights_status")
        acquired_at = _timestamp(metadata["acquired_at"], "acquired_at")

        related_projects = metadata["related_projects"]
        if not isinstance(related_projects, list) or not related_projects or len(set(related_projects)) != len(related_projects):
            raise PrivateEvidenceAdapterError("related_projects must be a non-empty unique list")
        related_projects = [_project_id(value, "related_projects[]") for value in related_projects]
        related_questions = metadata["related_questions"]
        if not isinstance(related_questions, list) or not related_questions or len(set(related_questions)) != len(related_questions):
            raise PrivateEvidenceAdapterError("related_questions must be a non-empty unique list")
        related_questions = [_question_id(value, "related_questions[]") for value in related_questions]

        creator = metadata.get("creator")
        if creator is not None:
            creator = _non_empty_string(creator, "creator")
        created_at = metadata.get("created_at", "unknown")
        if created_at != "unknown" and created_at is not None:
            created_at = _timestamp(created_at, "created_at")
        observed_by = metadata.get("observed_by")
        if observed_by is not None:
            observed_by = _non_empty_string(observed_by, "observed_by")
        return {
            "source_type": source_type,
            "source_location": source_location,
            "content_hash": content_hash,
            "rights_status": rights_status,
            "related_projects": sorted(related_projects),
            "related_questions": sorted(related_questions),
            "acquired_at": acquired_at,
            "creator": creator,
            "created_at": created_at,
            "observed_by": observed_by,
        }

    def _validate_signal(self, signal: Any) -> dict[str, Any]:
        if not isinstance(signal, dict):
            raise PrivateEvidenceAdapterError("derived_signal must be an object")
        forbidden = sorted(set(signal) & self.forbidden_input_fields)
        if forbidden:
            raise PrivateEvidenceAdapterError(f"derived_signal contains forbidden raw field: {forbidden[0]}")
        unknown = sorted(set(signal) - self.signal_fields)
        if unknown:
            raise PrivateEvidenceAdapterError(f"derived_signal contains unsupported field: {unknown[0]}")
        statement = _non_empty_string(signal.get("statement"), "derived_signal.statement")
        maximum = self.config.get("max_signal_statement_length")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
            raise PrivateEvidenceAdapterError("config/private-evidence.yaml: max_signal_statement_length must be positive")
        if len(statement) > maximum or "\n" in statement or "\r" in statement:
            raise PrivateEvidenceAdapterError("derived_signal.statement must be a compact single-line statement")
        scope = _non_empty_string(signal.get("scope"), "derived_signal.scope")
        epistemic_status = _non_empty_string(signal.get("epistemic_status"), "derived_signal.epistemic_status")
        if epistemic_status not in self.allowed_epistemic_statuses:
            raise PrivateEvidenceAdapterError("derived_signal.epistemic_status is not approved for private signals")
        return {
            "statement": statement,
            "scope": scope,
            "epistemic_status": epistemic_status,
            "valid_from": _date_or_null(signal.get("valid_from"), "derived_signal.valid_from"),
            "review_after": _date_or_null(signal.get("review_after"), "derived_signal.review_after"),
        }

    def build_records(
        self,
        metadata: dict[str, Any],
        derived_signal: dict[str, Any],
        *,
        evidence_id: str,
        claim_id: str,
    ) -> dict[str, dict[str, Any]]:
        if not re.fullmatch(r"EV[0-9]{3,}", evidence_id):
            raise PrivateEvidenceAdapterError("evidence_id must be a canonical EV ID")
        if not re.fullmatch(r"CL[0-9]{3,}", claim_id):
            raise PrivateEvidenceAdapterError("claim_id must be a canonical CL ID")
        safe_metadata = self._validate_metadata(metadata)
        safe_signal = self._validate_signal(derived_signal)
        evidence = {
            "id": evidence_id,
            "source_type": safe_metadata["source_type"],
            "source_location": safe_metadata["source_location"],
            "creator": safe_metadata["creator"],
            "created_at": safe_metadata["created_at"],
            "acquired_at": safe_metadata["acquired_at"],
            "content_hash": safe_metadata["content_hash"],
            "rights_status": safe_metadata["rights_status"],
            "sensitivity": next(iter(self.allowed_sensitivities)),
            "redistribution": self.config["derived_redistribution"],
            "related_projects": safe_metadata["related_projects"],
            "related_questions": safe_metadata["related_questions"],
            "extraction_status": self.config["derived_extraction_status"],
            "direct_observation": False,
            "observed_by": safe_metadata["observed_by"],
        }
        claim = {
            "id": claim_id,
            "statement": safe_signal["statement"],
            "type": self.config["derived_claim_type"],
            "evidence_ids": [evidence_id],
            "supporting_claims": [],
            "opposing_claims": [],
            "scope": safe_signal["scope"],
            "epistemic_status": safe_signal["epistemic_status"],
            "valid_from": safe_signal["valid_from"],
            "review_after": safe_signal["review_after"],
        }
        return {"evidence": evidence, "claim": claim}

    def import_records(
        self,
        target: str,
        metadata: dict[str, Any],
        derived_signal: dict[str, Any],
        *,
        evidence_id: str | None = None,
        claim_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(target, str) or not PROJECT_RE.fullmatch(target):
            raise PrivateEvidenceAdapterError("target must be project/<slug>")
        safe_metadata = self._validate_metadata(metadata)
        if safe_metadata["related_projects"] != [target]:
            raise PrivateEvidenceAdapterError("private evidence must belong only to the import target")
        safe_signal = self._validate_signal(derived_signal)
        projects_root = (self.repository_root / "projects").resolve()
        project = (projects_root / target.split("/", 1)[1]).resolve()
        if project.parent != projects_root or not project.is_dir():
            raise FileNotFoundError(f"project not found: {target}")
        evidence_path = project / "02_evidence" / "evidence-ledger.jsonl"
        claims_path = project / "03_knowledge" / "claims.jsonl"
        evidence_before = evidence_path.read_text(encoding="utf-8")
        claims_before = claims_path.read_text(encoding="utf-8")
        evidence_records = read_jsonl(evidence_path)
        claim_records = read_jsonl(claims_path)

        existing_evidence = next(
            (
                record
                for record in evidence_records
                if record.get("source_location") == safe_metadata["source_location"]
                and record.get("content_hash") == safe_metadata["content_hash"]
            ),
            None,
        )
        if existing_evidence is not None:
            existing_id = existing_evidence.get("id")
            matching_claims = [record for record in claim_records if existing_id in record.get("evidence_ids", [])]
            if len(matching_claims) != 1:
                raise PrivateEvidenceAdapterError("existing private evidence has no unique derived claim")
            expected = self.build_records(
                safe_metadata,
                safe_signal,
                evidence_id=existing_id,
                claim_id=matching_claims[0].get("id"),
            )
            if existing_evidence != expected["evidence"] or matching_claims[0] != expected["claim"]:
                raise PrivateEvidenceAdapterError("existing private evidence does not match the requested derived record")
            return {"imported": False, "evidence": existing_evidence, "claim": matching_claims[0]}

        used_ids = {record.get("id") for record in evidence_records + claim_records}
        generated_evidence_id = evidence_id or self._next_id("EV", used_ids)
        generated_claim_id = claim_id or self._next_id("CL", used_ids | {generated_evidence_id})
        records = self.build_records(
            safe_metadata,
            safe_signal,
            evidence_id=generated_evidence_id,
            claim_id=generated_claim_id,
        )
        if generated_evidence_id in used_ids or generated_claim_id in used_ids:
            raise PrivateEvidenceAdapterError("requested evidence or claim ID already exists")
        try:
            atomic_write_text(evidence_path, evidence_before + json.dumps(records["evidence"], ensure_ascii=False) + "\n")
            atomic_write_text(claims_path, claims_before + json.dumps(records["claim"], ensure_ascii=False) + "\n")
        except Exception:
            atomic_write_text(evidence_path, evidence_before)
            atomic_write_text(claims_path, claims_before)
            raise
        return {"imported": True, **records}

    @staticmethod
    def _next_id(prefix: str, used_ids: set[Any]) -> str:
        numbers = [int(match.group(2)) for value in used_ids if isinstance(value, str) and (match := ID_RE.fullmatch(value)) and match.group(1) == prefix]
        return f"{prefix}{max(numbers, default=0) + 1:03d}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Store private-source metadata and an approved derived signal without raw content.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--target", required=True)
    parser.add_argument("--metadata-json", type=Path, required=True)
    parser.add_argument("--signal-json", type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = json.loads(args.metadata_json.read_text(encoding="utf-8"))
        signal = json.loads(args.signal_json.read_text(encoding="utf-8"))
        result = PrivateEvidenceAdapter(args.root.resolve()).import_records(args.target, metadata, signal)
    except (OSError, json.JSONDecodeError, PrivateEvidenceAdapterError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
