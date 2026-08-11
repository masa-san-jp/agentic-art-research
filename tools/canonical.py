"""Canonical serialization helpers shared by handoff producers and validators."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a JSON-compatible value."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the repository hash representation for a canonical payload."""

    digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return f"sha256:{digest}"


def handoff_hash_payload(handoff: dict[str, Any]) -> dict[str, Any]:
    """Return the handoff payload whose hash excludes its self-referential integrity block."""

    return {key: value for key, value in handoff.items() if key != "integrity"}


def handoff_sha256(handoff: dict[str, Any]) -> str:
    """Hash a handoff without allowing the integrity field to hash itself."""

    return canonical_sha256(handoff_hash_payload(handoff))
