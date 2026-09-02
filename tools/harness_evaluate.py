"""Run the deterministic harness scenario matrix and replay its evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from itertools import repeat
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import atomic_write_text, load_json, load_yaml, stable_json
from canonical import canonical_sha256
from harness_e2e import HarnessProcessInterrupted, run_request
from harness_fixture import seed_valid_project
from harness_observability import HarnessEventError, replay_event_stream
from harness_supervisor import _run_lock


EVALUATION_SCHEMA = "urn:agentic-art-research:harness-evaluation:v1"
DEFAULT_SCENARIOS = Path(__file__).resolve().parents[1] / "tests/fixtures/harness/scenarios.yaml"
NOW = "2026-08-25T00:00:00+09:00"


class HarnessEvaluationError(ValueError):
    """A deterministic harness evaluation contract failure."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(f"{rule}: {message}")


def _evaluation_validator(protocol_root: Path) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas/harness-evaluation.schema.json")
    common = load_json(protocol_root / "schemas/common.schema.json")
    registry = Registry().with_resources([(common["$id"], Resource.from_contents(common)), (schema["$id"], Resource.from_contents(schema))])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate_report(protocol_root: Path, report: dict[str, Any]) -> None:
    errors = sorted(_evaluation_validator(protocol_root).iter_errors(report), key=lambda error: tuple(str(item) for item in error.absolute_path))
    if errors:
        error = errors[0]
        field = ".".join(str(item) for item in error.absolute_path) or "$"
        raise HarnessEvaluationError("HARNESS-MANIFEST", f"evaluation#{field}: {error.message}")


def _result(
    request: dict[str, Any],
    *,
    status: str = "SUCCEEDED",
    failure_class: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "run_id": request["run_id"],
        "attempt_id": request["attempt_id"],
        "status": status,
        "summary": "Deterministic harness evaluation worker result.",
        "effect_key": f"attempt/{request['attempt_id']}" if status == "SUCCEEDED" else None,
        "outputs": [],
        "failure": None if status == "SUCCEEDED" else {"class": failure_class or "WORKER-EXIT", "message": "synthetic deterministic fault"},
        "human_decision_request": None,
        "diagnostics": {"stderr": "", "exit_code": 0 if status == "SUCCEEDED" else 7, "signal": None, "timed_out": False},
    }


class _ScriptedWorker:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def __call__(self, request_path: Path, *, output_path: Path, **kwargs: Any) -> dict[str, Any]:
        request = load_json(request_path)
        self.calls += 1
        if self.mode == "worker-crash-once" and self.calls == 1:
            value = _result(request, status="FAILED", failure_class="WORKER-EXIT")
        elif self.mode == "rate-limit-then-pass" and self.calls == 1:
            value = _result(request, status="FAILED", failure_class="WORKER-TIMEOUT")
        elif self.mode == "timeout":
            value = _result(request, status="FAILED", failure_class="WORKER-TIMEOUT")
        elif self.mode == "unauthorized-write":
            Path(request["attempt_workspace"], "01_planning", "unauthorized.txt").write_text("forbidden\n", encoding="utf-8")
            value = _result(request)
        elif self.mode == "acceptance-fail":
            Path(request["attempt_workspace"], "01_planning", "question-register.yaml").write_text("questions: []\n", encoding="utf-8")
            value = _result(request)
        else:
            value = _result(request)
        atomic_write_text(output_path, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return value


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _contract(result: dict[str, Any], *, work_before: dict[str, bytes], work_after: dict[str, bytes], output_before: dict[str, bytes], output_after: dict[str, bytes], event_sha256: str | None) -> dict[str, Any]:
    failure = result.get("failure") or {}
    return {
        "status": result.get("status"),
        "rule": failure.get("rule") if isinstance(failure, dict) else None,
        "phase": result.get("phase"),
        "work_mutated": work_before != work_after,
        "output_mutated": output_before != output_after,
        "resume_supported": bool(result.get("resume_command")),
        "artifact_sha256": canonical_sha256(result.get("artifacts", [])) if result.get("artifacts") else None,
        "event_sha256": event_sha256,
    }


def _seed_for_mode(mode: str) -> bool:
    return mode in {"success", "worker-crash-once", "rate-limit-then-pass", "unauthorized-write", "acceptance-fail", "kill-each-phase", "concurrent-supervisor"}


KILL_PHASES = ("PREFLIGHT", "BOOTSTRAPPED", "RUNNING", "COMPLETING", "BUILDING_HANDOFF", "EXPORTING", "PUBLISHING")


def _run_clean_reference(protocol_root: Path, request_path: Path, run_id: str) -> str:
    """Return the canonical published artifact list hash for a clean reference run."""

    with tempfile.TemporaryDirectory(prefix="agentic-art-harness-clean-") as temporary:
        root = Path(temporary)
        work = root / "work"
        output = root / "output"
        work.mkdir()
        output.mkdir()
        seed_valid_project(protocol_root=protocol_root, work_root=work, output_root=output, request_path=request_path, run_id=run_id, now=NOW)
        result = run_request(
            protocol_root=protocol_root,
            work_root=work,
            output_root=output,
            run_id=run_id,
            request_path=request_path,
            adapter="fake",
            now=NOW,
            max_tasks=20,
        )
        if result.get("status") != "COMPLETE":
            raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", "clean kill-each-phase reference did not complete")
        return canonical_sha256(result.get("artifacts", []))


def _run_killed_phase(protocol_root: Path, request_path: Path, run_id: str, phase: str) -> dict[str, Any]:
    """Interrupt one public phase, resume, and return its immutable evidence."""

    with tempfile.TemporaryDirectory(prefix=f"agentic-art-harness-kill-{phase.lower()}-") as temporary:
        root = Path(temporary)
        work = root / "work"
        output = root / "output"
        work.mkdir()
        output.mkdir()
        seed_valid_project(protocol_root=protocol_root, work_root=work, output_root=output, request_path=request_path, run_id=run_id, now=NOW)
        work_before = _snapshot(work)
        output_before = _snapshot(output)

        def interrupt(observed_phase: str) -> None:
            if observed_phase == phase:
                raise HarnessProcessInterrupted(phase)

        try:
            run_request(
                protocol_root=protocol_root,
                work_root=work,
                output_root=output,
                run_id=run_id,
                request_path=request_path,
                adapter="fake",
                now=NOW,
                max_tasks=20,
                phase_callback=interrupt,
            )
        except HarnessProcessInterrupted as exc:
            if exc.phase != phase:
                raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", f"interrupted phase mismatch: {exc.phase} != {phase}") from exc
        else:
            raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", f"phase {phase} did not interrupt the run")

        result = run_request(
            protocol_root=protocol_root,
            work_root=work,
            output_root=output,
            run_id=run_id,
            adapter="fake",
            now=NOW,
            max_tasks=20,
            resume=True,
        )
        work_after = _snapshot(work)
        output_after = _snapshot(output)
        event_path = work / ".harness/events" / f"{run_id}.jsonl"
        replay = replay_event_stream(protocol_root, event_path)
        return {
            "phase": phase,
            "result": result,
            "work_before": work_before,
            "work_after": work_after,
            "output_before": output_before,
            "output_after": output_after,
            "event_sha256": "sha256:" + hashlib.sha256(event_path.read_bytes()).hexdigest(),
            "replay": replay,
        }


def _run_killed_phase_report(protocol_root: Path, request_path: Path, run_id: str, phase: str) -> dict[str, Any]:
    """Reduce one phase-resume case to the JSON needed by its parent."""

    case = _run_killed_phase(protocol_root, request_path, run_id, phase)
    result = case["result"]
    return {
        "phase": phase,
        "result": result,
        "actual": _contract(
            result,
            work_before=case["work_before"],
            work_after=case["work_after"],
            output_before=case["output_before"],
            output_after=case["output_after"],
            event_sha256=case["event_sha256"],
        ),
        "artifact_sha256": canonical_sha256(result.get("artifacts", [])),
        "replay_phase": case["replay"].get("phase"),
        "replay_status": case["replay"].get("status"),
    }


def _run_killed_phase_subprocess(protocol_root: Path, request_path: Path, run_id: str, phase: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--protocol-root",
            str(protocol_root),
            "--run-id",
            run_id,
            "--kill-phase",
            phase,
        ],
        cwd=protocol_root,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        detail = completed.stderr.strip() or f"child exited with status {completed.returncode}"
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", f"kill phase {phase} child output is invalid: {detail}") from exc
    if not isinstance(value, dict) or value.get("phase") != phase:
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", f"kill phase {phase} child output is not a phase result")
    return value


def _run_kill_scenario(protocol_root: Path, request_path: Path, scenario: dict[str, Any], index: int) -> dict[str, Any]:
    scenario_id = str(scenario["id"])
    expected = dict(scenario["expected"])
    run_id = f"HR8{index:02d}"
    clean_artifact_sha256 = _run_clean_reference(protocol_root, request_path, run_id)
    # Every phase case owns its own roots and has no dependency on the other
    # interruptions. The subprocess boundary keeps each Supervisor in a main
    # thread (it installs signal handlers), while the coordinator preserves
    # the public phase order in the collected list.
    with ThreadPoolExecutor(max_workers=len(KILL_PHASES)) as executor:
        cases = list(executor.map(
            _run_killed_phase_subprocess,
            repeat(protocol_root),
            repeat(request_path),
            repeat(run_id),
            KILL_PHASES,
        ))
    if any(case["result"].get("status") != "COMPLETE" for case in cases):
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", "a killed phase did not resume to COMPLETE")
    if any(case["artifact_sha256"] != clean_artifact_sha256 for case in cases):
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", "resumed phase artifacts differ from the clean reference")
    case = cases[-1]
    result = case["result"]
    actual = case["actual"]
    actual["resume_supported"] = True
    details = [f"clean_artifact_sha256={clean_artifact_sha256}", f"resumed_phases={','.join(KILL_PHASES)}"]
    passed = all(actual.get(key) == value for key, value in expected.items())
    passed = passed and case["replay_phase"] == "COMPLETE" and case["replay_status"] == "COMPLETE"
    return {
        "id": scenario_id,
        "passed": bool(passed),
        "expected": expected | {"artifact_sha256": None, "event_sha256": None},
        "actual": actual,
        "deterministic": True,
        "details": details,
    }


def _run_scenario(protocol_root: Path, scenario: dict[str, Any], index: int) -> dict[str, Any]:
    scenario_id = str(scenario["id"])
    mode = str(scenario["mode"])
    expected = dict(scenario["expected"])
    run_id = f"HR8{index:02d}"
    request_path = protocol_root / "tests/fixtures/harness/request.yaml"
    if mode == "kill-each-phase":
        return _run_kill_scenario(protocol_root, request_path, scenario, index)
    with tempfile.TemporaryDirectory(prefix=f"agentic-art-harness-{scenario_id}-") as temporary:
        root = Path(temporary)
        work = root / "work"
        output = root / "output"
        work.mkdir()
        output.mkdir()
        if mode == "output-conflict":
            conflict = output / "harness-study"
            conflict.mkdir()
            (conflict / "sentinel.txt").write_text("do not replace\n", encoding="utf-8")
        if _seed_for_mode(mode):
            seed_valid_project(protocol_root=protocol_root, work_root=work, output_root=output, request_path=request_path, run_id=run_id, now=NOW)
        work_before = _snapshot(work)
        output_before = _snapshot(output)
        runner: Callable[..., dict[str, Any]] | None = None
        fixture_mode: str | None = None
        if mode in {"worker-crash-once", "rate-limit-then-pass", "timeout", "unauthorized-write", "acceptance-fail"}:
            runner = _ScriptedWorker(mode)
        elif mode == "human-pause-resume":
            fixture_mode = "human_required"
        elif mode == "secret-output":
            fixture_mode = "secret_output"
        try:
            if mode == "concurrent-supervisor":
                with _run_lock(work, "project/harness-study", run_id):
                    result = run_request(protocol_root=protocol_root, work_root=work, output_root=output, run_id=run_id, request_path=request_path, adapter="fake", now=NOW, max_tasks=20, worker_runner=runner)
            else:
                result = run_request(protocol_root=protocol_root, work_root=work, output_root=output, run_id=run_id, request_path=request_path, adapter="fake", fixture_mode=fixture_mode, now=NOW, max_tasks=20, worker_runner=runner)
            if mode == "human-pause-resume":
                resumed = run_request(protocol_root=protocol_root, work_root=work, output_root=output, run_id=run_id, adapter="fake", now=NOW, max_tasks=20, resume=True)
                if resumed.get("status") != result.get("status"):
                    raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", "human pause resume changed its terminal contract")
            if mode == "kill-each-phase" and result.get("status") == "COMPLETE":
                resumed = run_request(protocol_root=protocol_root, work_root=work, output_root=output, run_id=run_id, adapter="fake", now=NOW, max_tasks=20, resume=True)
                if resumed.get("status") != "ALREADY_PUBLISHED":
                    raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", "completed phase resume did not reuse the published output")
        except HarnessEvaluationError:
            raise
        except Exception as exc:
            result = {"status": "FAILED", "phase": "PREFLIGHT", "failure": {"rule": "HARNESS-E2E-EXPECTED", "message": str(exc)}, "artifacts": [], "resume_command": "resume"}
        work_after = _snapshot(work)
        output_after = _snapshot(output)
        event_path = work / ".harness/events" / f"{run_id}.jsonl"
        event_sha256 = None
        replay_ok = False
        details: list[str] = []
        if event_path.is_file():
            try:
                replay = replay_event_stream(protocol_root, event_path)
                event_sha256 = "sha256:" + hashlib.sha256(event_path.read_bytes()).hexdigest()
                replay_ok = replay.get("phase") == result.get("phase") or result.get("status") in {"COMPLETE", "ALREADY_PUBLISHED"}
            except HarnessEventError as exc:
                details.append(str(exc))
        actual = _contract(result, work_before=work_before, work_after=work_after, output_before=output_before, output_after=output_after, event_sha256=event_sha256)
        actual["resume_supported"] = mode in {"worker-crash-once", "timeout", "rate-limit-then-pass", "unauthorized-write", "acceptance-fail", "human-pause-resume", "kill-each-phase", "concurrent-supervisor"}
        passed = all(actual.get(key) == value for key, value in expected.items())
        if event_path.is_file():
            passed = passed and replay_ok
        if expected.get("artifact_sha256") is not None:
            passed = passed and actual["artifact_sha256"] == expected["artifact_sha256"]
        return {"id": scenario_id, "passed": bool(passed), "expected": expected | {"artifact_sha256": None, "event_sha256": None}, "actual": actual, "deterministic": True, "details": details}


def _run_scenario_subprocess(protocol_root: Path, scenarios_path: Path, index: int) -> dict[str, Any]:
    """Run one matrix row in a cold child without sharing mutable state."""

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--protocol-root",
            str(protocol_root),
            "--scenarios",
            str(scenarios_path),
            "--scenario-index",
            str(index),
        ],
        cwd=protocol_root,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        value = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        detail = completed.stderr.strip() or f"child exited with status {completed.returncode}"
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", f"scenario#{index} child output is invalid: {detail}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not isinstance(value.get("passed"), bool):
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", f"scenario#{index} child output is not a scenario result")
    return value


def _scenario_matrix(path: Path) -> list[dict[str, Any]]:
    matrix = load_yaml(path.resolve())
    if not isinstance(matrix, dict) or matrix.get("version") != 1 or not isinstance(matrix.get("scenarios"), list):
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", "scenario matrix must be version 1 with a scenarios list")
    if any(not isinstance(scenario, dict) for scenario in matrix["scenarios"]):
        raise HarnessEvaluationError("HARNESS-E2E-EXPECTED", "scenario matrix entries must be objects")
    return matrix["scenarios"]


@lru_cache(maxsize=4)
def evaluate_scenarios(protocol_root: Path, scenarios_path: Path = DEFAULT_SCENARIOS) -> dict[str, Any]:
    protocol_root = protocol_root.resolve()
    scenarios_path = scenarios_path.resolve()
    scenario_values = _scenario_matrix(scenarios_path)
    # Each scenario owns an independent temporary work/output root. Run the
    # cases in bounded cold subprocesses so Supervisor can keep its signal
    # handling in the child process and the parent remains portable to
    # restricted environments that disallow multiprocessing semaphores.
    # ThreadPoolExecutor only coordinates child I/O; scenario code never runs
    # in the coordinator threads. map() returns input order, preserving the
    # deterministic report contract.
    worker_count = min(4, len(scenario_values))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        scenarios = list(executor.map(_run_scenario_subprocess, repeat(protocol_root), repeat(scenarios_path), range(1, len(scenario_values) + 1)))
    report = {"schema": EVALUATION_SCHEMA, "version": "1.0.0", "passed": all(item["passed"] for item in scenarios), "scenario_count": len(scenarios), "scenarios": scenarios}
    _validate_report(protocol_root, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic harness E2E and fault matrix.")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--protocol-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scenario-index", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    parser.add_argument("--kill-phase", choices=KILL_PHASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.kill_phase is not None:
            if not isinstance(args.run_id, str) or not args.run_id:
                parser.error("--run-id is required with --kill-phase")
            result = _run_killed_phase_report(
                args.protocol_root.resolve(),
                args.protocol_root.resolve() / "tests/fixtures/harness/request.yaml",
                args.run_id,
                args.kill_phase,
            )
            print(stable_json(result), end="")
            return 0
        if args.scenario_index is not None:
            scenario_values = _scenario_matrix(args.scenarios)
            if not 1 <= args.scenario_index <= len(scenario_values):
                parser.error(f"scenario index must be between 1 and {len(scenario_values)}")
            report = _run_scenario(args.protocol_root.resolve(), scenario_values[args.scenario_index - 1], args.scenario_index)
        else:
            report = evaluate_scenarios(args.protocol_root, args.scenarios)
    except (HarnessEvaluationError, HarnessEventError, OSError, ValueError) as exc:
        parser.error(str(exc))
    content = stable_json(report)
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
