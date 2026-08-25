#!/usr/bin/env python3
"""Deterministic, offline worker used by the adapter contract tests."""

from __future__ import annotations

import json
import os
import signal
import sys
import time


def result(request: dict[str, object], *, status: str = "SUCCEEDED") -> dict[str, object]:
    attempt_id = str(request["attempt_id"])
    run_id = str(request["run_id"])
    if status == "HUMAN_REQUIRED":
        return {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "attempt_id": attempt_id,
            "status": status,
            "summary": "A typed human decision is required.",
            "effect_key": None,
            "outputs": [],
            "failure": None,
            "human_decision_request": {
                "id": "DR001",
                "question": "Which reviewed direction should continue?",
                "options": ["direction-a", "direction-b"],
            },
            "diagnostics": {
                "stderr": "",
                "exit_code": 0,
                "signal": None,
                "timed_out": False,
            },
        }
    return {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "status": status,
        "summary": "Offline fake worker completed the requested attempt.",
        "effect_key": f"attempt/{run_id}/{attempt_id}",
        "outputs": [
            {
                "id": "OUT001",
                "kind": "REPORT",
                "value": {"mode": "offline", "task_id": request["task_id"]},
            }
        ],
        "failure": None,
        "human_decision_request": None,
        "diagnostics": {
            "stderr": "",
            "exit_code": 0,
            "signal": None,
            "timed_out": False,
        },
    }


def main() -> int:
    request = json.load(sys.stdin)
    mode = request.get("fixture_mode", "normal")
    if mode == "timeout":
        time.sleep(60)
        return 0
    if mode == "exit":
        sys.stderr.write("synthetic worker exit\n")
        return 7
    if mode == "signal":
        os.kill(os.getpid(), signal.SIGTERM)
        return 143
    if mode == "invalid_json":
        sys.stdout.write("{not-json\n")
        return 0
    if mode == "unknown_field":
        value = result(request)
        value["unexpected"] = True
        sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")
        return 0
    if mode == "stdout_oversize":
        sys.stdout.write("x" * 100000)
        return 0
    if mode == "stderr_oversize":
        sys.stderr.write("diagnostic-" + ("x" * 100000))
        return 0
    if mode == "secret_output":
        fixture_secret = "sk-" + ("x" * 24)
        sys.stdout.write(fixture_secret + "\n")
        sys.stderr.write("credential=" + ("y" * 24) + "\n")
        return 0
    if mode == "human_required":
        value = result(request, status="HUMAN_REQUIRED")
    else:
        value = result(request)
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
