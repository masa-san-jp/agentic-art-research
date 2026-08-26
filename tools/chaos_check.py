from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from _common import ROOT, load_yaml, read_jsonl, stable_json
from new_project import create_project
from task_runtime import TaskRuntimeError, claim_next, complete, fail, initialize_runtime, load_runtime, resume
from validate import validate_repository


class ChaosCheckError(ValueError):
    """Raised when a deterministic chaos scenario cannot be executed."""


def _make_root(repository_root: Path) -> Path:
    temporary = Path(tempfile.mkdtemp(prefix="agentic-art-chaos-"))
    for name in ("templates", "config", "schemas"):
        shutil.copytree(repository_root / name, temporary / name)
    (temporary / "projects").mkdir()
    (temporary / "data").mkdir()
    return temporary


def _runtime_root(repository_root: Path, slug: str) -> Path:
    root = _make_root(repository_root)
    create_project(root, slug, "Chaos Fixture", "creator/synthetic", created_at="2026-08-11T00:00:00+09:00")
    return root


def _api_stop(repository_root: Path) -> dict[str, Any]:
    root = _runtime_root(repository_root, "api-stop")
    try:
        initialize_runtime(
            root,
            "project/api-stop",
            [{"id": "TASK001", "depends_on": [], "max_attempts": 2}, {"id": "TASK002", "depends_on": ["TASK001"]}],
            initialized_at="2026-08-11T00:00:00+09:00",
        )

        class StoppedApi:
            def fetch(self) -> None:
                raise ConnectionError("synthetic upstream unavailable")

        worker = "chaos-api-worker"
        claim = claim_next(root, "project/api-stop", worker, now="2026-08-11T00:00:00+09:00")
        if claim is None:
            raise ChaosCheckError("API stop scenario could not claim a task")
        failure_class = None
        try:
            StoppedApi().fetch()
        except ConnectionError:
            failure_class = "TIMEOUT"
        if failure_class is None:
            raise ChaosCheckError("API stop fault was not raised")
        fail(root, "project/api-stop", "TASK001", worker, claim["lease_token"], failure_class, "synthetic upstream stopped", now="2026-08-11T00:00:01+09:00")
        claim = claim_next(root, "project/api-stop", worker, now="2026-08-11T00:00:02+09:00")
        if claim is None:
            raise ChaosCheckError("API stop retry could not claim a task")
        fail(root, "project/api-stop", "TASK001", worker, claim["lease_token"], failure_class, "synthetic upstream stopped again", now="2026-08-11T00:00:03+09:00")
        runtime = load_runtime(root, "project/api-stop")
        passed = runtime["tasks"]["TASK001"]["status"] == "FAILED" and runtime["tasks"]["TASK002"]["status"] == "BLOCKED"
        return {"id": "api-stop", "passed": passed, "failure_class": failure_class, "dependent_status": runtime["tasks"]["TASK002"]["status"]}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _broken_jsonl(repository_root: Path) -> dict[str, Any]:
    root = _runtime_root(repository_root, "broken-jsonl")
    try:
        path = root / "projects/broken-jsonl/03_knowledge/claims.jsonl"
        path.write_text("{broken jsonl\n", encoding="utf-8")
        findings = validate_repository(root)
        detected = any(item.rule == "JSONL" and item.path.endswith("claims.jsonl") for item in findings)
        return {"id": "broken-jsonl", "passed": detected, "finding_rule": "JSONL" if detected else None}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _interrupted_lease(repository_root: Path) -> dict[str, Any]:
    root = _runtime_root(repository_root, "interrupted-lease")
    try:
        initialize_runtime(root, "project/interrupted-lease", [{"id": "TASK001", "depends_on": [], "max_attempts": 2}], initialized_at="2026-08-11T00:00:00+09:00")
        first = claim_next(root, "project/interrupted-lease", "worker-a", now="2026-08-11T00:00:00+09:00", lease_seconds=5)
        if first is None:
            raise ChaosCheckError("interrupted lease scenario could not claim a task")
        recovered = resume(root, "project/interrupted-lease", now="2026-08-11T00:00:06+09:00")
        stale_rejected = False
        try:
            complete(root, "project/interrupted-lease", "TASK001", "worker-a", first["lease_token"], {"value": "stale"}, now="2026-08-11T00:00:06+09:00")
        except TaskRuntimeError:
            stale_rejected = True
        second = claim_next(root, "project/interrupted-lease", "worker-b", now="2026-08-11T00:00:06+09:00")
        if second is None:
            raise ChaosCheckError("recovered lease could not be reclaimed")
        result = complete(root, "project/interrupted-lease", "TASK001", "worker-b", second["lease_token"], {"value": "committed"}, now="2026-08-11T00:00:07+09:00")
        # The expiry returns the attempt it took, so the task that ran twice has
        # spent one attempt and recorded one expiry. Counting the expiry as an
        # attempt is what made a slow task look like a failing one.
        passed = (
            recovered["recovered"] == ["TASK001"]
            and stale_rejected
            and result["status"] == "SUCCEEDED"
            and result["attempts"] == 1
            and result.get("lease_expiries") == 1
        )
        return {
            "id": "interrupted-lease",
            "passed": passed,
            "stale_rejected": stale_rejected,
            "recovered": recovered["recovered"],
            "attempts": result["attempts"],
            "lease_expiries": result.get("lease_expiries"),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _duplicate_effect(repository_root: Path) -> dict[str, Any]:
    root = _runtime_root(repository_root, "duplicate-effect")
    try:
        initialize_runtime(root, "project/duplicate-effect", [{"id": "TASK001", "depends_on": [], "max_attempts": 1}], initialized_at="2026-08-11T00:00:00+09:00")
        claim = claim_next(root, "project/duplicate-effect", "worker", now="2026-08-11T00:00:00+09:00")
        if claim is None:
            raise ChaosCheckError("duplicate effect scenario could not claim a task")
        first = complete(root, "project/duplicate-effect", "TASK001", "worker", claim["lease_token"], {"value": "one"}, effect_key="TASK001", now="2026-08-11T00:00:01+09:00")
        repeated = complete(root, "project/duplicate-effect", "TASK001", "worker", claim["lease_token"], {"value": "ignored"}, effect_key="TASK001", now="2026-08-11T00:00:02+09:00")
        events = read_jsonl(root / "projects/duplicate-effect/07_runtime/run-log.jsonl")
        success_events = [event for event in events if event.get("event_type") == "TASK_SUCCEEDED" and event.get("task_id") == "TASK001"]
        passed = first == repeated and len(success_events) == 1
        return {"id": "duplicate-effect", "passed": passed, "success_events": len(success_events), "effect_key": first["effect_key"]}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _harness_fault_matrix(repository_root: Path) -> dict[str, Any]:
    path = repository_root / "tests/fixtures/harness/scenarios.yaml"
    expected = {
        "success",
        "worker-crash-once",
        "timeout",
        "rate-limit-then-pass",
        "unauthorized-write",
        "acceptance-fail",
        "human-pause-resume",
        "kill-each-phase",
        "output-conflict",
        "secret-output",
        "concurrent-supervisor",
    }
    try:
        matrix = load_yaml(path)
        scenarios = matrix.get("scenarios") if isinstance(matrix, dict) else None
        ids = {item.get("id") for item in scenarios} if isinstance(scenarios, list) and all(isinstance(item, dict) for item in scenarios) else set()
        valid = ids == expected and all(
            isinstance(item.get("expected"), dict)
            and {"status", "rule", "phase", "work_mutated", "output_mutated", "resume_supported"}.issubset(item["expected"])
            for item in scenarios
            if isinstance(item, dict)
        )
        return {"id": "harness-fault-matrix", "passed": valid, "scenario_count": len(ids), "expected_count": len(expected)}
    except Exception as exc:
        return {"id": "harness-fault-matrix", "passed": False, "error": str(exc)}


SCENARIOS: tuple[Callable[[Path], dict[str, Any]], ...] = (_api_stop, _broken_jsonl, _interrupted_lease, _duplicate_effect, _harness_fault_matrix)


def run_chaos_suite(repository_root: Path = ROOT) -> dict[str, Any]:
    repository_root = repository_root.resolve()
    scenarios = [scenario(repository_root) for scenario in SCENARIOS]
    return {"version": 1, "scenarios": scenarios, "passed": all(item["passed"] for item in scenarios)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic failure, interruption, and duplicate-effect recovery scenarios.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = run_chaos_suite(args.root)
    except (OSError, ValueError, ChaosCheckError) as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
