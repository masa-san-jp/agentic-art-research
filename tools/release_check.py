from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from _common import ROOT, load_yaml, stable_json
from evaluate import evaluate_offline_fixture


class ReleaseCheckError(ValueError):
    """Raised when the release policy is invalid."""


def _config(root: Path) -> dict[str, Any]:
    value = load_yaml(root / "config" / "release.yaml") or {}
    if not isinstance(value, dict):
        raise ReleaseCheckError("config/release.yaml must be a mapping")
    for key in ("required_paths", "required_schema_paths", "required_workflow_snippets"):
        if not isinstance(value.get(key), list) or any(not isinstance(item, str) or not item for item in value[key]):
            raise ReleaseCheckError(f"config/release.yaml: {key} must be a string list")
    runs = value.get("ci_runs")
    if not isinstance(runs, int) or isinstance(runs, bool) or runs <= 0:
        raise ReleaseCheckError("config/release.yaml: ci_runs must be a positive integer")
    item_count = value.get("mvp_item_count")
    if not isinstance(item_count, int) or isinstance(item_count, bool) or item_count <= 0:
        raise ReleaseCheckError("config/release.yaml: mvp_item_count must be a positive integer")
    return value


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise ReleaseCheckError(f"release path escapes repository: {relative}")
    return candidate


def _required_paths(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for relative in config["required_paths"]:
        path = _safe_path(root, relative)
        results.append({"path": relative, "present": path.exists()})
    return results


def _schema_paths(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for relative in config["required_schema_paths"]:
        path = _safe_path(root, relative)
        results.append({"path": relative, "present": path.is_file()})
    return results


def _workflow_check(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    workflow = _safe_path(root, ".github/workflows/validate.yml")
    content = workflow.read_text(encoding="utf-8") if workflow.is_file() else ""
    snippets = config["required_workflow_snippets"]
    missing = [snippet for snippet in snippets if snippet not in content]
    return {"path": ".github/workflows/validate.yml", "missing_snippets": missing, "passed": not missing and workflow.is_file()}


def _spec_check(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    path = _safe_path(root, "docs/20260811-agentic-art-research-system-design-specification.md")
    content = path.read_text(encoding="utf-8") if path.is_file() else ""
    section = content.split("### 19.2 本システムのMVP完了", 1)
    section_text = section[1].split("## 20.", 1)[0] if len(section) == 2 else ""
    checked = len(re.findall(r"^- \[x\] ", section_text, flags=re.MULTILINE))
    expected = config["mvp_item_count"]
    return {"checked_items": checked, "expected_items": expected, "passed": checked == expected}


def _command_result(root: Path, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    result: dict[str, Any] = {"command": command, "returncode": completed.returncode, "passed": completed.returncode == 0}
    if completed.returncode != 0:
        combined = (completed.stdout + "\n" + completed.stderr).strip()
        result["output_tail"] = combined[-2000:]
    return result


def _ci_runs(root: Path, fixture: Path, count: int) -> list[dict[str, Any]]:
    commands = [
        [sys.executable, "-m", "compileall", "-q", "tools", "tests"],
        [sys.executable, "tools/validate.py", "--root", str(root), "--check"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        [sys.executable, "tools/build_graph.py", "--root", str(root), "--check"],
        [sys.executable, "tools/evaluate.py", "--root", str(root), "--offline-fixture", str(fixture)],
    ]
    runs: list[dict[str, Any]] = []
    for number in range(1, count + 1):
        commands_result = [_command_result(root, command) for command in commands]
        runs.append({"run": number, "passed": all(item["passed"] for item in commands_result), "commands": commands_result})
        if not runs[-1]["passed"]:
            break
    return runs


def check_release(root: Path = ROOT, fixture: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    config = _config(root)
    fixture = (fixture or root / "tests/fixtures/harmony").resolve()
    required_paths = _required_paths(root, config)
    schemas = _schema_paths(root, config)
    workflow = _workflow_check(root, config)
    spec = _spec_check(root, config)
    fixture_result = evaluate_offline_fixture(root, fixture)
    runs = _ci_runs(root, fixture, config["ci_runs"])
    path_check = all(item["present"] for item in required_paths)
    schema_check = all(item["present"] for item in schemas)
    ci_check = len(runs) == config["ci_runs"] and all(item["passed"] for item in runs)
    mvp_items = [
        {"id": "directory-structure", "passed": path_check, "evidence": [item["path"] for item in required_paths if not item["present"]]},
        {"id": "seven-schemas", "passed": schema_check, "evidence": [item["path"] for item in schemas if not item["present"]]},
        {"id": "new-project", "passed": path_check, "evidence": ["tools/new_project.py", "templates/project"]},
        {"id": "validator", "passed": ci_check, "evidence": ["tools/validate.py --check"]},
        {"id": "dependency-graph", "passed": ci_check, "evidence": ["tools/build_graph.py --check"]},
        {"id": "bundles", "passed": path_check, "evidence": ["tools/bundle.py"]},
        {"id": "impact", "passed": path_check, "evidence": ["tools/impact.py"]},
        {"id": "github-actions", "passed": workflow["passed"], "evidence": workflow["missing_snippets"]},
        {"id": "offline-sample", "passed": fixture_result["passed"], "evidence": ["tests/fixtures/harmony"]},
        {"id": "private-derived-signal", "passed": fixture_result["gates"]["privacy"]["passed"], "evidence": ["tools/private_evidence.py"]},
    ]
    passed = spec["passed"] and ci_check and all(item["passed"] for item in mvp_items)
    return {
        "version": 1,
        "release_version": config["release_version"],
        "mvp_items": mvp_items,
        "spec_mvp": spec,
        "workflow": workflow,
        "offline_fixture": {"name": fixture.name, "passed": fixture_result["passed"], "gates": fixture_result["gates"]},
        "ci_runs_requested": config["ci_runs"],
        "ci_runs": runs,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local v1.0.0 release checklist without publishing anything.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--offline-fixture", type=Path)
    args = parser.parse_args()
    try:
        result = check_release(args.root, args.offline_fixture)
    except (OSError, ReleaseCheckError, ValueError) as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
