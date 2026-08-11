#!/usr/bin/env python3
"""Verify the repository-local v1.0.0 release gate."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from _common import PROJECT_REQUIRED_FILES, ROOT, atomic_write_text, load_json, stable_json
from build_graph import build_graph
from bundle import build_bundle
from chaos_check import run_chaos_suite
from docs_check import check_documentation
from evaluate import evaluate_offline_fixture
from impact import impact_report
from new_project import create_project
from run_project import run_offline_fixture
from security_check import scan_advanced_security
from validate import validate_repository


RELEASE_SCHEMA = "urn:agentic-art-research:release-check:v1"
SCHEMA_NAMES = (
    "project-manifest",
    "evidence",
    "claim",
    "insight",
    "decision",
    "requirement",
    "research-state",
    "completion-report",
)
SPEC_PATH = "docs/20260811-agentic-art-research-system-design-specification.md"
EXPECTED_CI_RUNS = 3


class ReleaseCheckError(ValueError):
    """Raised when release evidence cannot be evaluated."""


def _check(name: str, passed: bool, details: list[str]) -> dict[str, Any]:
    return {"id": name, "passed": passed, "details": details}


def _workspace(source_root: Path) -> tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory(prefix="agentic-art-release-")
    root = Path(temporary.name)
    for name in ("templates", "config", "schemas"):
        shutil.copytree(source_root / name, root / name)
    (root / "projects").mkdir()
    (root / "data").mkdir()
    return temporary, root


def _ci_check(path: Path) -> tuple[bool, list[str]]:
    try:
        evidence = load_json(path)
    except Exception as exc:
        return False, [f"could not read CI evidence: {exc}"]
    runs = evidence.get("runs") if isinstance(evidence, dict) else None
    workflow = evidence.get("workflow") if isinstance(evidence, dict) else None
    if not isinstance(runs, list):
        return False, ["CI evidence must contain a runs list"]
    valid = [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get("workflow", workflow) == "validate"
        and run.get("conclusion") == "success"
        and isinstance(run.get("id"), int)
        and isinstance(run.get("url"), str)
    ]
    unique_ids = {run["id"] for run in valid}
    passed = len(valid) >= EXPECTED_CI_RUNS and len(unique_ids) == len(valid)
    return passed, [f"successful validate runs={len(valid)}", f"required={EXPECTED_CI_RUNS}"]


def _spec_check(root: Path) -> tuple[bool, list[str]]:
    path = root / SPEC_PATH
    if not path.is_file():
        return False, [f"missing specification: {SPEC_PATH}"]
    section = path.read_text(encoding="utf-8").split("### 19.2", 1)[-1].split("## 20", 1)[0]
    checked = sum(line.startswith("- [x] ") for line in section.splitlines())
    unchecked = sum(line.startswith("- [ ] ") for line in section.splitlines())
    return checked >= 10 and unchecked == 0, [f"checked={checked}", f"unchecked={unchecked}"]


def check_release(root: Path, fixture: Path, ci_evidence: Path) -> dict[str, Any]:
    root = root.resolve()
    fixture = fixture.resolve()
    structure_paths = (
        "AGENTS.md",
        "PLANS.md",
        "README.md",
        "config",
        "docs",
        "execution",
        "schemas",
        "templates",
        "tests",
        "tools",
    )
    structure = _check(
        "structure",
        all((root / relative).exists() for relative in structure_paths),
        [f"missing={relative}" for relative in structure_paths if not (root / relative).exists()],
    )
    schemas = _check(
        "schemas",
        all((root / "schemas" / f"{name}.schema.json").is_file() for name in SCHEMA_NAMES),
        [f"missing={name}.schema.json" for name in SCHEMA_NAMES if not (root / "schemas" / f"{name}.schema.json").is_file()],
    )

    temporary, probe_root = _workspace(root)
    try:
        project = create_project(probe_root, "release-probe", "Release Probe", created_at="2026-08-11T00:00:00+00:00")
        findings = validate_repository(probe_root)
        new_project_check = _check(
            "new_project_and_validate",
            all((project / relative).exists() for relative in PROJECT_REQUIRED_FILES) and not findings,
            [f"missing_or_invalid={len(findings)}"],
        )
        fixture_project = run_offline_fixture(probe_root, "harmony-study", fixture)
        graph = build_graph(probe_root)
        human = build_bundle(probe_root, "project/harmony-study", "human")
        production = build_bundle(probe_root, "project/harmony-study", "production-agent")
        bundle_check = _check(
            "graph_bundle_impact",
            bool(graph.get("nodes"))
            and "Harmony Study" in human
            and "RQ001" in production
            and impact_report(graph, "EV001").get("found") is True,
            [f"nodes={len(graph.get('nodes', []))}", f"human_bytes={len(human)}", f"production_bytes={len(production)}"],
        )
        completion = load_json(fixture_project / "07_runtime" / "completion-report.json")
        sample_check = _check(
            "sample_terminal",
            completion.get("status") in {"COMPLETE", "COMPLETE_WITH_GAPS"},
            [f"status={completion.get('status')}"],
        )
    finally:
        temporary.cleanup()

    evaluation_temporary, evaluation_root = _workspace(root)
    try:
        evaluation = evaluate_offline_fixture(evaluation_root, fixture)
    finally:
        evaluation_temporary.cleanup()
    evaluation_check = _check(
        "e2e_evaluation",
        evaluation.get("passed") is True,
        [f"passed_checks={sum(item.get('passed') is True for item in evaluation.get('checks', []))}"],
    )
    spec_passed, spec_details = _spec_check(root)
    spec_check = _check("specification_mvp", spec_passed, spec_details)
    ci_passed, ci_details = _ci_check(ci_evidence)
    ci_check = _check("ci_three_runs", ci_passed, ci_details)
    security_findings = scan_advanced_security(root)
    security_check = _check(
        "advanced_security",
        not security_findings,
        [f"findings={len(security_findings)}"] + [f"{finding.rule}: {finding.path}" for finding in security_findings],
    )
    chaos_result = run_chaos_suite(root)
    chaos_check = _check(
        "chaos_recovery",
        chaos_result.get("passed") is True,
        [f"scenarios={len(chaos_result.get('scenarios', []))}"],
    )
    documentation_findings = check_documentation(root)
    documentation_check = _check(
        "operations_documentation",
        not documentation_findings,
        [f"findings={len(documentation_findings)}"] + documentation_findings,
    )
    checks = [
        structure,
        schemas,
        new_project_check,
        bundle_check,
        sample_check,
        evaluation_check,
        spec_check,
        ci_check,
        security_check,
        chaos_check,
        documentation_check,
    ]
    return {
        "schema": RELEASE_SCHEMA,
        "version": "1.0.1",
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "ci_evidence": ci_evidence.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the repository-local v1.0.0 release gate.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--ci-evidence", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    try:
        content = stable_json(check_release(args.root, args.offline_fixture, args.ci_evidence))
    except (FileNotFoundError, ReleaseCheckError, ValueError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
