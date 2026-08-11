#!/usr/bin/env python3
"""Run the research-side release gate for the production handoff extension."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from _common import ROOT, atomic_write_text, load_json, load_yaml, stable_json
from import_production_result import ResultImportError, import_production_result
from release_check import check_release
from run_project import run_offline_fixture
from validate import validate_repository


CHECK_SCHEMA = "urn:agentic-art-research:handoff-release-check:v1"
CHECK_VERSION = "1.0.0"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class HandoffReleaseCheckError(ValueError):
    """Raised when the handoff release check cannot be evaluated."""


def _check(
    check_id: str,
    passed: bool,
    status: str,
    details: list[str],
    *,
    required_for_research_gate: bool,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "passed": passed,
        "status": status,
        "details": details,
        "required_for_research_gate": required_for_research_gate,
    }


def _workspace(source_root: Path) -> tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory(prefix="agentic-art-handoff-release-")
    root = Path(temporary.name)
    for name in ("templates", "config", "schemas"):
        shutil.copytree(source_root / name, root / name)
    (root / "projects").mkdir()
    (root / "data").mkdir()
    return temporary, root


def _workspace_relative_error(exc: ResultImportError, root: Path) -> str:
    """Render probe errors without leaking a nondeterministic temporary path."""

    rendered = str(exc)
    raw_path = str(getattr(exc, "path", ""))
    if not raw_path:
        return rendered
    try:
        path = Path(raw_path).resolve()
        workspace = root.resolve()
        if path.is_relative_to(workspace):
            return rendered.replace(raw_path, path.relative_to(workspace).as_posix(), 1)
    except (OSError, RuntimeError, ValueError):
        pass
    return rendered.replace(str(root.resolve()), "<workspace>", 1)


def _handoff_fixture_contract(root: Path, fixture: Path) -> dict[str, Any]:
    path = fixture / "scenario.yaml"
    try:
        scenario = load_yaml(path)
    except Exception as exc:
        return _check(
            "handoff_fixture_contract",
            False,
            "INVALID",
            [f"could not load {path}: {exc}"],
            required_for_research_gate=True,
        )
    if not isinstance(scenario, dict):
        return _check(
            "handoff_fixture_contract",
            False,
            "INVALID",
            ["scenario.yaml must contain a mapping"],
            required_for_research_gate=True,
        )
    source_fixture = scenario.get("source_fixture")
    handoff = scenario.get("handoff")
    expected_paths = handoff.get("expected_bundle_paths") if isinstance(handoff, dict) else None
    resolved_source = (fixture / str(source_fixture)).resolve() if isinstance(source_fixture, str) else None
    safe_paths = isinstance(expected_paths, list) and all(
        isinstance(item, str) and not Path(item).is_absolute() and ".." not in Path(item).parts for item in expected_paths
    )
    required_paths = {
        "manifest.yaml",
        "production-handoff.yaml",
        "provenance.yaml",
        "schemas/production-handoff.schema.json",
    }
    passed = (
        scenario.get("scenario") == "harmony-handoff"
        and scenario.get("version") == 1
        and resolved_source == (fixture.parent / "harmony").resolve()
        and resolved_source.is_dir()
        and isinstance(handoff, dict)
        and isinstance(handoff.get("id"), str)
        and bool(re.fullmatch(r"HO[0-9]{3,}", handoff["id"]))
        and safe_paths
        and required_paths.issubset(set(expected_paths or []))
    )
    details = [
        f"scenario={scenario.get('scenario')!r}",
        f"source_fixture={source_fixture!r}",
        f"expected_bundle_paths={len(expected_paths) if isinstance(expected_paths, list) else 0}",
    ]
    return _check(
        "handoff_fixture_contract",
        passed,
        "PASS" if passed else "INVALID",
        details,
        required_for_research_gate=True,
    )


def _feedback_schema_boundary(root: Path) -> dict[str, Any]:
    temporary, probe_root = _workspace(root)
    try:
        # The boundary probe must exercise an unavailable production-owned
        # schema even when the real research root has a valid snapshot. A
        # missing result input tests FEEDBACK-INPUT, not EXTERNAL-SCHEMA.
        policy_path = probe_root / "config" / "handoff-policy.yaml"
        policy = load_yaml(policy_path) or {}
        configured_schema = policy.get("production_result_schema_path") if isinstance(policy, dict) else None
        if isinstance(configured_schema, str) and configured_schema:
            schema_path = (probe_root / configured_schema).resolve()
            if probe_root.resolve() in schema_path.parents and schema_path.is_file():
                schema_path.unlink()
        try:
            import_production_result(probe_root, probe_root / "missing-production-result.json", dry_run=True)
        except ResultImportError as exc:
            passed = exc.rule == "EXTERNAL-SCHEMA"
            return _check(
                "feedback_schema_boundary",
                passed,
                "FAIL_CLOSED" if passed else "WRONG_FAILURE",
                [f"rule={exc.rule}", _workspace_relative_error(exc, probe_root)],
                required_for_research_gate=True,
            )
        return _check(
            "feedback_schema_boundary",
            False,
            "UNEXPECTED_SUCCESS",
            ["feedback import did not stop when the production-owned schema snapshot was absent"],
            required_for_research_gate=True,
        )
    except Exception as exc:
        return _check(
            "feedback_schema_boundary",
            False,
            "ERROR",
            [str(exc)],
            required_for_research_gate=True,
        )
    finally:
        temporary.cleanup()


def _backward_compatibility(root: Path, offline_fixture: Path) -> dict[str, Any]:
    temporary, probe_root = _workspace(root)
    try:
        project = run_offline_fixture(probe_root, "harmony-study", offline_fixture)
        findings = validate_repository(probe_root, "project/harmony-study")
        manifest = load_yaml(project / "manifest.yaml") or {}
        entry_points = manifest.get("entry_points", {}) if isinstance(manifest, dict) else {}
        passed = (
            not findings
            and manifest.get("workflow_mode") != "PRODUCTION_HANDOFF"
            and "production_handoff" not in entry_points
        )
        details = [
            f"findings={len(findings)}",
            f"workflow_mode={manifest.get('workflow_mode')!r}",
            f"production_handoff_entry={entry_points.get('production_handoff')!r}",
        ]
        return _check(
            "research_only_backward_compatibility",
            passed,
            "PASS" if passed else "REGRESSION",
            details,
            required_for_research_gate=True,
        )
    except Exception as exc:
        return _check(
            "research_only_backward_compatibility",
            False,
            "ERROR",
            [str(exc)],
            required_for_research_gate=True,
        )
    finally:
        temporary.cleanup()


def _production_result_schema_snapshot(root: Path) -> dict[str, Any]:
    policy_path = root / "config" / "handoff-policy.yaml"
    try:
        policy = load_yaml(policy_path) or {}
    except Exception as exc:
        return _check(
            "production_result_schema_snapshot",
            False,
            "INVALID",
            [f"could not load {policy_path}: {exc}"],
            required_for_research_gate=False,
        )
    raw_path = policy.get("production_result_schema_path") if isinstance(policy, dict) else None
    versions = policy.get("production_result_schema_versions") if isinstance(policy, dict) else None
    source = policy.get("production_result_schema_source") if isinstance(policy, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip() or not isinstance(versions, list) or not versions or not isinstance(source, dict) or not source:
        return _check(
            "production_result_schema_snapshot",
            False,
            "PENDING_EXTERNAL_SCHEMA",
            ["production-owned result schema snapshot and provenance are not configured"],
            required_for_research_gate=False,
        )
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        return _check(
            "production_result_schema_snapshot",
            False,
            "PENDING_EXTERNAL_SCHEMA",
            [f"schema snapshot is unavailable: {candidate}"],
            required_for_research_gate=False,
        )
    expected_sha = source.get("sha256")
    commit = source.get("commit")
    if not isinstance(commit, str) or not SHA_PATTERN.fullmatch(commit) or not isinstance(expected_sha, str) or not SHA256_PATTERN.fullmatch(expected_sha):
        return _check(
            "production_result_schema_snapshot",
            False,
            "INVALID_PROVENANCE",
            ["schema source must include a 40-character commit and raw sha256"],
            required_for_research_gate=False,
        )
    actual_sha = f"sha256:{hashlib.sha256(candidate.read_bytes()).hexdigest()}"
    if actual_sha != expected_sha:
        return _check(
            "production_result_schema_snapshot",
            False,
            "HASH_MISMATCH",
            [f"actual={actual_sha}", f"configured={expected_sha}"],
            required_for_research_gate=False,
        )
    try:
        schema = load_json(candidate)
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        return _check(
            "production_result_schema_snapshot",
            False,
            "INVALID_SCHEMA",
            [str(exc)],
            required_for_research_gate=False,
        )
    return _check(
        "production_result_schema_snapshot",
        True,
        "SNAPSHOT_VALID",
        [f"version_count={len(versions)}", f"source_commit={commit}"],
        required_for_research_gate=False,
    )


def check_handoff_release(
    root: Path,
    offline_fixture: Path,
    handoff_fixture: Path,
    ci_evidence: Path,
) -> dict[str, Any]:
    root = root.resolve()
    offline_fixture = offline_fixture.resolve()
    handoff_fixture = handoff_fixture.resolve()
    base = check_release(root, offline_fixture, ci_evidence)
    checks = [
        _check(
            "base_release",
            base.get("passed") is True,
            "PASS" if base.get("passed") is True else "FAIL",
            [f"checks={len(base.get('checks', []))}", f"passed={base.get('passed')!r}"],
            required_for_research_gate=True,
        ),
        _handoff_fixture_contract(root, handoff_fixture),
        _feedback_schema_boundary(root),
        _backward_compatibility(root, offline_fixture),
        _production_result_schema_snapshot(root),
    ]
    research_checks = [check for check in checks if check["required_for_research_gate"]]
    return {
        "schema": CHECK_SCHEMA,
        "version": CHECK_VERSION,
        "passed": all(check["passed"] for check in research_checks),
        "schema_snapshot_ready": next(check["passed"] for check in checks if check["id"] == "production_result_schema_snapshot"),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the research-side production handoff release gate.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--offline-fixture", type=Path)
    parser.add_argument("--handoff-fixture", type=Path)
    parser.add_argument("--ci-evidence", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--require-schema-snapshot", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    result = check_handoff_release(
        root,
        (args.offline_fixture or root / "tests" / "fixtures" / "harmony"),
        (args.handoff_fixture or root / "tests" / "fixtures" / "harmony-handoff"),
        (args.ci_evidence or root / "execution" / "ci-evidence.json"),
    )
    content = stable_json(result)
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    if not result["passed"] or (args.require_schema_snapshot and not result["schema_snapshot_ready"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
