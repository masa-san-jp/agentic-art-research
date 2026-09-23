#!/usr/bin/env python3
"""Complete one attempt with a locally hosted model.

The adapter contract is provider neutral: read one attempt request on stdin,
write only the declared targets inside the attempt workspace, and print one
attempt result on stdout. Nothing here reaches the network beyond the local
model endpoint, so the worker carries no credential and needs no environment.

The previous attempt's acceptance report, when there is one, is fed back into
the prompt. A gate that already said what was missing is the cheapest possible
instruction, and reusing it is what lets a second attempt differ from the first.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ENDPOINT = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "gpt-oss:20b"
REQUEST_TIMEOUT_SECONDS = 600


def _result(request: dict[str, Any], status: str, summary: str, outputs: list[dict[str, Any]], failure: dict[str, str] | None) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "run_id": request.get("run_id", "HR000"),
        "attempt_id": request.get("attempt_id", "AT000"),
        "status": status,
        "summary": summary,
        "effect_key": f"attempt/{request.get('run_id')}/{request.get('attempt_id')}" if status == "SUCCEEDED" else None,
        "outputs": outputs,
        "failure": failure,
        "human_decision_request": None,
        "diagnostics": {"stderr": "", "exit_code": 0, "signal": None, "timed_out": False},
    }


def _previous_gate_failures(workspace: Path) -> list[str]:
    attempts_root = workspace.parent.parent
    reports = sorted(attempts_root.glob("AT*/acceptance-report.json"))
    failures: list[str] = []
    for report_path in reports:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for gate in report.get("gates", []):
            if gate.get("status") == "FAIL":
                failures.append(
                    f"{gate.get('id')}: {gate.get('kind')} on {gate.get('path')} expected {gate.get('expected')}, got {gate.get('actual')}"
                )
                failures.extend(f"  {finding}" for finding in gate.get("findings", []))
    return failures


def _target_state(workspace: Path, targets: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for target in targets:
        path = workspace / target["path"]
        current = path.read_text(encoding="utf-8") if path.is_file() else "(missing)"
        lines.append(f"--- {target['path']} (mode {target.get('mode', 'UPDATE')}) ---\n{current}")
    return "\n".join(lines)


def _prompt(request: dict[str, Any], workspace: Path) -> str:
    targets = request.get("write_targets", [])
    sections = [
        f"Role: {request.get('role')}",
        f"Task: {request.get('task_id')} in {request.get('project_id')}",
        f"Context: {request.get('context', {}).get('summary', '')}",
        "Protocol steps you are executing:",
        "\n\n".join(request.get("instructions", [])),
        "Files you may write, with their current content:",
        _target_state(workspace, targets),
    ]
    failures = _previous_gate_failures(workspace)
    if failures:
        sections.append("A previous attempt failed these acceptance gates. Fix exactly these:\n" + "\n".join(failures))
    sections.append(
        "Answer with one JSON object: {\"files\": {\"<path>\": <content>}, \"summary\": \"<one sentence>\"}. "
        "Every key of files must be one of the paths listed above, written exactly as listed, even where a "
        "finding below spells the same file with a projects/ prefix. For a .yaml path the content is the whole "
        "document as a JSON object; for a .jsonl path it is a list of records, one per line. Keep every field "
        "that the current content already has. Invent no source, no URL, and no evidence."
    )
    return "\n\n".join(section for section in sections if section)


def _ask_model(model: str, prompt: str, endpoint: str = ENDPOINT) -> dict[str, Any]:
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
    ).encode("utf-8")
    call = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(call, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body = json.loads(response.read().decode("utf-8"))
    return json.loads(body["message"]["content"])


def _decoded(record: Any) -> Any:
    """A record handed back as a JSON string is still that record; written quoted, it breaks the ledger."""
    if isinstance(record, str):
        try:
            return json.loads(record)
        except json.JSONDecodeError:
            return record
    return record


def _resolve_target(answered: str, allowed: set[str]) -> str | None:
    """Findings name a file as projects/<slug>/…, targets name it project-relative; both mean one file."""
    if answered in allowed:
        return answered
    segments = answered.split("/")
    for target in allowed:
        target_segments = target.split("/")
        if len(segments) > len(target_segments) and segments[-len(target_segments):] == target_segments:
            return target
    return None


def _write(workspace: Path, targets: list[dict[str, Any]], files: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {target["path"] for target in targets}
    outputs: list[dict[str, Any]] = []
    for index, (answered, content) in enumerate(sorted(files.items()), 1):
        relative = _resolve_target(answered, allowed)
        if relative is None:
            continue
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".jsonl"):
            records = [_decoded(record) for record in (content if isinstance(content, list) else [content])]
            if any(not isinstance(record, dict) for record in records):
                continue
            text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
        else:
            text = yaml.safe_dump(content, allow_unicode=True, sort_keys=False)
        path.write_text(text, encoding="utf-8")
        outputs.append({"id": f"OUT{index:03d}", "kind": "ARTIFACT", "path": relative, "value": {"bytes": len(text.encode("utf-8"))}})
    return outputs


def main() -> int:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps(_result({}, "FAILED", "The attempt request was not JSON.", [], {"class": "WORKER-PROTOCOL", "message": "unreadable request"})))
        return 0

    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    endpoint = sys.argv[2] if len(sys.argv) > 2 else ENDPOINT
    workspace = Path(request["attempt_workspace"])
    try:
        answer = _ask_model(model, _prompt(request, workspace), endpoint)
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps(_result(request, "FAILED", "The local model did not return a usable answer.", [], {"class": "WORKER-EXIT", "message": type(exc).__name__})))
        return 0

    files = answer.get("files")
    if not isinstance(files, dict) or not files:
        print(json.dumps(_result(request, "FAILED", "The model answered without any file content.", [], {"class": "WORKER-EXIT", "message": "no files in answer"})))
        return 0

    outputs = _write(workspace, request.get("write_targets", []), files)
    if not outputs:
        print(json.dumps(_result(request, "FAILED", "The model wrote only paths this attempt may not touch.", [], {"class": "WORKER-EXIT", "message": "no allowed target written"})))
        return 0

    summary = str(answer.get("summary") or "The worker wrote the declared targets.")[:500]
    print(json.dumps(_result(request, "SUCCEEDED", summary, outputs, None)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
