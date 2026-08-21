#!/usr/bin/env python3
"""Append one run-log event, checked before it is written.

    python3 tools/log_event.py project/<slug> SEARCH_ATTEMPT --question Q001 --strategy "museum catalogue"

The stopping policy counts these events. An event written with a missing
field is not a smaller event: it is a search the policy cannot see, so the
policy keeps saying there is more to do. The check happens here rather than
after the fact, because a run-log the policy silently ignores looks exactly
like a project that never searched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _common import ROOT, InputParseError, read_jsonl

EVENT_TYPES = ("SEARCH_ATTEMPT", "SOURCE_REVIEWED", "EVIDENCE_ROUND", "ANSWER_FOUND")


class EventError(ValueError):
    """The event cannot be recorded as given."""


def _project(root: Path, target: str) -> Path:
    if not target.startswith("project/") or target.count("/") != 1:
        raise EventError("target must be project/<slug>")
    project = root / "projects" / target.split("/", 1)[1]
    if not project.is_dir():
        raise EventError(f"project not found: {target}")
    return project


def _next_event_id(events: list[dict[str, Any]]) -> str:
    highest = 0
    for event in events:
        identifier = str(event.get("event_id", ""))
        if identifier.startswith("RUN-EVT-") and identifier[8:].isdigit():
            highest = max(highest, int(identifier[8:]))
    return f"RUN-EVT-{highest + 1:06d}"


def build_event(
    events: list[dict[str, Any]],
    event_type: str,
    *,
    occurred_at: str,
    question_id: str | None = None,
    strategy_id: str | None = None,
    source_id: str | None = None,
    evidence_id: str | None = None,
    worker_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise EventError(f"unknown event type {event_type!r}; use one of {', '.join(EVENT_TYPES)}")
    if not question_id:
        raise EventError(f"{event_type} requires --question; the stopping policy counts per question")
    if event_type == "SEARCH_ATTEMPT" and not strategy_id:
        raise EventError("SEARCH_ATTEMPT requires --strategy; the policy counts distinct strategies, not attempts")
    if event_type == "SOURCE_REVIEWED" and not (source_id or evidence_id):
        raise EventError("SOURCE_REVIEWED requires --source or --evidence; the policy counts distinct sources")
    event: dict[str, Any] = {
        "event_id": _next_event_id(events),
        "event_type": event_type,
        "occurred_at": occurred_at,
        "question_id": question_id,
    }
    for key, value in (
        ("strategy_id", strategy_id),
        ("source_id", source_id),
        ("evidence_id", evidence_id),
        ("worker_id", worker_id),
        ("note", note),
    ):
        if value:
            event[key] = value
    return event


def append_event(root: Path, target: str, event_type: str, **fields: Any) -> dict[str, Any]:
    project = _project(root, target)
    log_path = project / "07_runtime" / "run-log.jsonl"
    events = read_jsonl(log_path) if log_path.is_file() else []
    event = build_event(events, event_type, **fields)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="Append one checked run-log event.")
    parser.add_argument("target")
    parser.add_argument("event_type", choices=EVENT_TYPES)
    parser.add_argument("--question", required=True, help="Q### この事象がどの質問に属するか")
    parser.add_argument("--strategy", help="SEARCH_ATTEMPT に必須。探索戦略の識別子")
    parser.add_argument("--source", help="SOURCE_REVIEWED に必須（--evidence でも可）")
    parser.add_argument("--evidence", help="EV### 既に台帳へ入れた証拠の識別子")
    parser.add_argument("--worker", help="実行した主体")
    parser.add_argument("--note", help="人が後から読むための短い補足")
    parser.add_argument("--occurred-at", required=True, help="RFC 3339。時刻は呼び出し側が渡す")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        event = append_event(
            args.root.resolve(),
            args.target,
            args.event_type,
            occurred_at=args.occurred_at,
            question_id=args.question,
            strategy_id=args.strategy,
            source_id=args.source,
            evidence_id=args.evidence,
            worker_id=args.worker,
            note=args.note,
        )
    except (EventError, InputParseError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
