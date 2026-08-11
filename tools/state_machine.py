from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json


class TransitionError(ValueError):
    """Raised when an event cannot be applied to the current lifecycle state."""


@dataclass(frozen=True)
class ReplayResult:
    initial_status: str
    final_status: str
    event_count: int
    transition_count: int


@dataclass(frozen=True)
class StateMachine:
    statuses: frozenset[str]
    transitions: dict[str, frozenset[str]]

    @classmethod
    def from_vocabulary(cls, vocabulary: dict[str, Any]) -> "StateMachine":
        statuses = frozenset(value for value in vocabulary.get("project_statuses", []) if isinstance(value, str))
        raw_transitions = vocabulary.get("project_state_transitions", {})
        transitions = {
            source: frozenset(target for target in targets if isinstance(target, str))
            for source, targets in raw_transitions.items()
            if isinstance(source, str) and isinstance(targets, list)
        }
        return cls(statuses, transitions)

    def is_allowed(self, from_status: str, to_status: str) -> bool:
        return from_status in self.statuses and to_status in self.statuses and to_status in self.transitions.get(from_status, frozenset())

    def apply(self, current_status: str, event: dict[str, Any]) -> str:
        from_status = event.get("from_status")
        to_status = event.get("to_status")
        if not isinstance(from_status, str) or not isinstance(to_status, str):
            raise TransitionError("STATE_TRANSITION event requires string from_status and to_status")
        if from_status != current_status:
            raise TransitionError(f"transition starts at {from_status}, current status is {current_status}")
        if from_status not in self.statuses or to_status not in self.statuses:
            raise TransitionError(f"unknown lifecycle transition {from_status!r} -> {to_status!r}")
        if not self.is_allowed(from_status, to_status):
            raise TransitionError(f"illegal lifecycle transition {from_status} -> {to_status}")
        return to_status

    def replay(self, initial_status: str, events: Iterable[dict[str, Any]]) -> ReplayResult:
        current = initial_status
        seen_event_ids: set[str] = set()
        event_count = 0
        transition_count = 0
        for event in events:
            event_count += 1
            event_id = event.get("event_id", event.get("id"))
            if not isinstance(event_id, str) or not event_id:
                raise TransitionError(f"event {event_count} has no event_id")
            if event_id in seen_event_ids:
                raise TransitionError(f"duplicate event ID: {event_id}")
            seen_event_ids.add(event_id)
            if event.get("event_type") != "STATE_TRANSITION" and "from_status" not in event and "to_status" not in event:
                continue
            current = self.apply(current, event)
            transition_count += 1
        return ReplayResult(initial_status, current, event_count, transition_count)


def load_state_machine(root: Path) -> StateMachine:
    vocabulary = load_yaml(root / "config" / "vocabularies.yaml") or {}
    if not isinstance(vocabulary, dict):
        raise ValueError("config/vocabularies.yaml must be a mapping")
    return StateMachine.from_vocabulary(vocabulary)


def replay_project(root: Path, target: str) -> ReplayResult:
    if not target.startswith("project/") or target.count("/") != 1:
        raise ValueError("target must be project/<slug>")
    project = (root / "projects" / target.split("/", 1)[1]).resolve()
    if not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    state = load_json(project / "07_runtime" / "research-state.json")
    events = read_jsonl(project / "07_runtime" / "run-log.jsonl")
    transition_events = [event for event in events if event.get("event_type") == "STATE_TRANSITION" or "from_status" in event or "to_status" in event]
    machine = load_state_machine(root.resolve())
    if transition_events:
        initial_status = transition_events[0].get("from_status")
        if not isinstance(initial_status, str):
            raise TransitionError("first state transition has no from_status")
    else:
        initial_status = state.get("status")
    if not isinstance(initial_status, str):
        raise TransitionError("cannot infer initial lifecycle status")
    result = machine.replay(initial_status, events)
    if result.final_status != state.get("status"):
        raise TransitionError(f"replayed status {result.final_status} differs from research-state status {state.get('status')}")
    return result


def transition_project(root: Path, target: str, to_status: str, *, event_id: str, updated_at: str) -> dict[str, Any]:
    if not target.startswith("project/") or target.count("/") != 1:
        raise ValueError("target must be project/<slug>")
    project = (root / "projects" / target.split("/", 1)[1]).resolve()
    if not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    manifest_path = project / "manifest.yaml"
    state_path = project / "07_runtime" / "research-state.json"
    log_path = project / "07_runtime" / "run-log.jsonl"
    manifest = load_yaml(manifest_path) or {}
    state = load_json(state_path)
    current_status = (manifest.get("project") or {}).get("status")
    if current_status != state.get("status"):
        raise TransitionError("manifest and research-state statuses must match")
    machine = load_state_machine(root.resolve())
    event = {"event_id": event_id, "event_type": "STATE_TRANSITION", "from_status": current_status, "to_status": to_status}
    new_status = machine.apply(current_status, event)
    existing_events = read_jsonl(log_path)
    if any(item.get("event_id", item.get("id")) == event_id for item in existing_events):
        raise TransitionError(f"duplicate event ID: {event_id}")
    before = {path: path.read_text(encoding="utf-8") for path in (manifest_path, state_path, log_path)}
    try:
        manifest["project"]["status"] = new_status
        manifest["project"]["updated_at"] = updated_at
        state["status"] = new_status
        state["updated_at"] = updated_at
        state["last_event_id"] = event_id
        atomic_write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False))
        atomic_write_text(state_path, stable_json(state))
        atomic_write_text(log_path, before[log_path] + json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        for path, content in before.items():
            atomic_write_text(path, content)
        raise
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay or apply the canonical project lifecycle state machine.")
    parser.add_argument("target")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--to-status")
    parser.add_argument("--event-id")
    parser.add_argument("--updated-at")
    args = parser.parse_args()
    try:
        if args.replay:
            result = replay_project(args.root.resolve(), args.target)
            print(stable_json(result.__dict__), end="")
        else:
            if not args.to_status or not args.event_id or not args.updated_at:
                parser.error("provide --replay or --to-status, --event-id, and --updated-at")
            event = transition_project(
                args.root.resolve(), args.target, args.to_status, event_id=args.event_id, updated_at=args.updated_at
            )
            print(stable_json(event), end="")
    except (FileNotFoundError, TransitionError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
