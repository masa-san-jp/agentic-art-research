#!/usr/bin/env python3
"""Capture a clean, commit-pinned production result schema snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from _common import ROOT, atomic_write_text, load_json, load_yaml, stable_json


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class SchemaSnapshotError(ValueError):
    """Raised when a production schema cannot be captured safely."""

    def __init__(self, message: str, *, rule: str = "EXTERNAL-SCHEMA-SNAPSHOT") -> None:
        self.rule = rule
        super().__init__(message)

    def __str__(self) -> str:
        return f"[{self.rule}] {self.args[0]}"


def _git(source_repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise SchemaSnapshotError(f"git {' '.join(args)} failed for {source_repo}: {detail}", rule="EXTERNAL-SCHEMA-SOURCE") from exc
    return completed.stdout.strip()


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaSnapshotError("acquired_at must be RFC 3339", rule="EXTERNAL-SCHEMA-PROVENANCE") from exc
    if parsed.tzinfo is None:
        raise SchemaSnapshotError("acquired_at must include a timezone", rule="EXTERNAL-SCHEMA-PROVENANCE")
    return value


def _safe_relative(value: str, *, field: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SchemaSnapshotError(f"{field} must be a non-escaping relative path", rule="EXTERNAL-SCHEMA-PATH")
    return path


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _update_policy(
    root: Path,
    *,
    output: Path,
    source_repository: str,
    commit: str,
    acquired_at: str,
    sha256: str,
    version: str,
) -> None:
    policy_path = root / "config" / "handoff-policy.yaml"
    try:
        policy = load_yaml(policy_path) or {}
    except Exception as exc:
        raise SchemaSnapshotError(f"could not load {policy_path}: {exc}", rule="EXTERNAL-SCHEMA-POLICY") from exc
    if not isinstance(policy, dict):
        raise SchemaSnapshotError("handoff policy must be a mapping", rule="EXTERNAL-SCHEMA-POLICY")
    relative_output = output.relative_to(root).as_posix()
    current_path = policy.get("production_result_schema_path")
    current_source = policy.get("production_result_schema_source")
    current_versions = policy.get("production_result_schema_versions")
    if current_path not in (None, relative_output, "schemas/external/production-result.v1.schema.json"):
        raise SchemaSnapshotError("existing production result schema path points to a different snapshot", rule="EXTERNAL-SCHEMA-POLICY")
    if isinstance(current_source, dict) and current_source:
        if current_source.get("commit") != commit or current_source.get("sha256") != sha256:
            raise SchemaSnapshotError("existing production schema provenance differs from the requested snapshot", rule="EXTERNAL-SCHEMA-POLICY")
    versions = list(current_versions) if isinstance(current_versions, list) else []
    if any(not isinstance(item, str) for item in versions):
        raise SchemaSnapshotError("production_result_schema_versions must contain strings", rule="EXTERNAL-SCHEMA-POLICY")
    if version not in versions:
        versions.append(version)
    policy["production_result_schema_path"] = relative_output
    policy["production_result_schema_versions"] = versions
    policy["production_result_schema_source"] = {
        "repository": source_repository,
        "commit": commit,
        "acquired_at": acquired_at,
        "sha256": sha256,
    }
    atomic_write_text(policy_path, yaml.safe_dump(policy, sort_keys=False, allow_unicode=True))


def snapshot_schema(
    root: Path,
    source_repo: Path,
    source_schema: str,
    output: Path,
    *,
    source_repository: str,
    commit: str,
    acquired_at: str,
    version: str,
) -> dict[str, Any]:
    root = root.resolve()
    source_repo = source_repo.resolve()
    if not source_repo.is_dir():
        raise SchemaSnapshotError(f"source repository was not found: {source_repo}", rule="EXTERNAL-SCHEMA-SOURCE")
    if not isinstance(source_repository, str) or not source_repository.strip():
        raise SchemaSnapshotError("source_repository is required", rule="EXTERNAL-SCHEMA-PROVENANCE")
    if not SHA_PATTERN.fullmatch(commit):
        raise SchemaSnapshotError("commit must be a 40-character lowercase SHA", rule="EXTERNAL-SCHEMA-PROVENANCE")
    acquired_at = _timestamp(acquired_at)
    source_relative = _safe_relative(source_schema, field="source_schema")
    source_path = (source_repo / source_relative).resolve()
    if source_repo not in source_path.parents or not source_path.is_file():
        raise SchemaSnapshotError("source schema must be an existing file inside the source repository", rule="EXTERNAL-SCHEMA-PATH")

    actual_commit = _git(source_repo, "rev-parse", "HEAD")
    if actual_commit != commit:
        raise SchemaSnapshotError(
            f"source HEAD {actual_commit} does not match requested commit {commit}",
            rule="EXTERNAL-SCHEMA-SOURCE",
        )
    dirty = _git(source_repo, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise SchemaSnapshotError("source repository is dirty; capture only from a clean commit", rule="EXTERNAL-SCHEMA-SOURCE")

    raw = source_path.read_bytes()
    try:
        schema = json.loads(raw)
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise SchemaSnapshotError(f"source schema is not a valid Draft 2020-12 schema: {exc}", rule="EXTERNAL-SCHEMA-SCHEMA") from exc
    sha256 = f"sha256:{hashlib.sha256(raw).hexdigest()}"

    destination = output if output.is_absolute() else root / output
    destination = destination.resolve()
    if root not in destination.parents or destination.parent.name != "external" or destination.parent.parent.name != "schemas":
        raise SchemaSnapshotError("output must be below schemas/external in the research repository", rule="EXTERNAL-SCHEMA-PATH")
    if destination.exists() and destination.read_bytes() != raw:
        raise SchemaSnapshotError("refusing to overwrite a different existing schema snapshot", rule="EXTERNAL-SCHEMA-OVERWRITE")
    if not destination.exists():
        _atomic_write_bytes(destination, raw)
    _update_policy(
        root,
        output=destination,
        source_repository=source_repository,
        commit=commit,
        acquired_at=acquired_at,
        sha256=sha256,
        version=version,
    )
    return {
        "status": "SNAPSHOT_CAPTURED",
        "path": destination.relative_to(root).as_posix(),
        "schema_version": version,
        "source_repository": source_repository,
        "source_commit": commit,
        "acquired_at": acquired_at,
        "sha256": sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a clean, commit-pinned production result schema.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--source-schema", required=True, help="Path relative to --source-repo")
    parser.add_argument("--source-repository", required=True, help="Canonical repository identifier")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--acquired-at", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=Path("schemas/external/production-result.v1.schema.json"))
    args = parser.parse_args()
    try:
        result = snapshot_schema(
            args.root,
            args.source_repo,
            args.source_schema,
            args.output,
            source_repository=args.source_repository,
            commit=args.commit,
            acquired_at=args.acquired_at,
            version=args.version,
        )
    except SchemaSnapshotError as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
