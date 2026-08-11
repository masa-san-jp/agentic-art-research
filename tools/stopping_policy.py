from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from _common import ROOT, atomic_write_text, load_json, load_yaml, read_jsonl, stable_json, yaml_list


QUESTION_TERMINAL_STATUSES = frozenset({"ANSWERED", "UNRESOLVED", "BLOCKED", "CANCELLED"})
QUESTION_OPEN_STATUS = "OPEN"
SEARCH_EVENTS = frozenset({"SEARCH_ATTEMPT", "SEARCH_ATTEMPTED"})
SOURCE_EVENTS = frozenset({"SOURCE_REVIEWED", "SOURCE_ACCESSED"})
ANSWER_EVENTS = frozenset({"ANSWER_FOUND", "QUESTION_ANSWERED"})


class StoppingPolicyError(ValueError):
    """Raised when stopping inputs or policy values are not contract-valid."""


@dataclass(frozen=True)
class StoppingPolicy:
    defaults: Mapping[str, int | bool]
    terminal_question_statuses: frozenset[str] = QUESTION_TERMINAL_STATUSES

    @classmethod
    def from_root(cls, root: Path) -> "StoppingPolicy":
        policy = load_yaml(root / "config" / "stopping-policy.yaml") or {}
        defaults = policy.get("defaults") if isinstance(policy, dict) else None
        if not isinstance(defaults, dict):
            raise StoppingPolicyError("config/stopping-policy.yaml: defaults must be a mapping")
        required = (
            "max_search_strategies_per_question",
            "max_sources_reviewed_per_question",
            "max_identical_failure_retries",
            "saturation_rounds_without_new_evidence",
            "default_sufficient_answers",
        )
        validated: dict[str, int | bool] = {}
        for key in required:
            value = defaults.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise StoppingPolicyError(f"config/stopping-policy.yaml: {key} must be a positive integer")
            validated[key] = value
        vocabulary = load_yaml(root / "config" / "vocabularies.yaml") or {}
        terminal = vocabulary.get("question_terminal_statuses") if isinstance(vocabulary, dict) else None
        if not isinstance(terminal, list) or not terminal or any(not isinstance(value, str) for value in terminal):
            raise StoppingPolicyError("config/vocabularies.yaml: question_terminal_statuses must be a non-empty list")
        return cls(validated, frozenset(terminal))

    def limits(self, question: Mapping[str, Any]) -> dict[str, int]:
        condition = question.get("stop_condition", {})
        if condition is None:
            condition = {}
        if not isinstance(condition, dict):
            raise StoppingPolicyError(f"question {question.get('id')!r}: stop_condition must be a mapping")
        values = {
            "sufficient_answers": condition.get("sufficient_answers", self.defaults["default_sufficient_answers"]),
            "max_search_strategies": condition.get(
                "max_search_strategies", self.defaults["max_search_strategies_per_question"]
            ),
            "max_sources_reviewed": condition.get(
                "max_sources_reviewed", self.defaults["max_sources_reviewed_per_question"]
            ),
            "max_identical_failure_retries": self.defaults["max_identical_failure_retries"],
            "saturation_rounds_without_new_evidence": self.defaults["saturation_rounds_without_new_evidence"],
        }
        limits: dict[str, int] = {}
        for key, value in values.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise StoppingPolicyError(f"question {question.get('id')!r}: {key} must be a positive integer")
            limits[key] = value
        return limits


@dataclass(frozen=True)
class QuestionMetrics:
    answers: int = 0
    search_strategies: frozenset[str] = frozenset()
    sources_reviewed: frozenset[str] = frozenset()
    saturation_rounds_without_new_evidence: int = 0
    failure_counts: Mapping[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "answers": self.answers,
            "search_strategies": sorted(self.search_strategies),
            "search_strategy_count": len(self.search_strategies),
            "sources_reviewed": sorted(self.sources_reviewed),
            "sources_reviewed_count": len(self.sources_reviewed),
            "saturation_rounds_without_new_evidence": self.saturation_rounds_without_new_evidence,
            "failure_counts": {key: self.failure_counts[key] for key in sorted(self.failure_counts)},
        }


def _project(root: Path, target: str) -> Path:
    if not isinstance(target, str) or not target.startswith("project/") or target.count("/") != 1:
        raise StoppingPolicyError("target must be project/<slug>")
    project = (root / "projects" / target.split("/", 1)[1]).resolve()
    projects_root = (root / "projects").resolve()
    if projects_root not in project.parents or not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def _validate_timestamp(value: str) -> str:
    normalized = value[:-1] + "+00:00" if isinstance(value, str) and value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise StoppingPolicyError("evaluated_at must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StoppingPolicyError("evaluated_at must include a timezone")
    return value


def _positive_count(value: Any, field_name: str, *, allow_zero: bool = True) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or (value < 0 if allow_zero else value <= 0):
        minimum = "non-negative" if allow_zero else "positive"
        raise StoppingPolicyError(f"{field_name} must be a {minimum} integer")
    return value


def metrics_from_events(events: Iterable[dict[str, Any]], question_ids: Iterable[str]) -> dict[str, QuestionMetrics]:
    question_set = set(question_ids)
    values: dict[str, dict[str, Any]] = {
        question_id: {
            "answers": 0,
            "strategies": set(),
            "sources": set(),
            "saturation": 0,
            "failures": Counter(),
        }
        for question_id in question_set
    }
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict):
            raise StoppingPolicyError(f"run-log event {index} must be an object")
        question_id = event.get("question_id")
        if question_id is None:
            continue
        if not isinstance(question_id, str) or question_id not in question_set:
            raise StoppingPolicyError(f"run-log event {index}: unknown question_id {question_id!r}")
        current = values[question_id]
        event_type = event.get("event_type")
        if event_type in SEARCH_EVENTS:
            strategy_id = event.get("strategy_id")
            if not isinstance(strategy_id, str) or not strategy_id:
                raise StoppingPolicyError(f"run-log event {index}: search event requires strategy_id")
            current["strategies"].add(strategy_id)
        elif event_type in SOURCE_EVENTS:
            source_id = event.get("source_id", event.get("evidence_id"))
            if not isinstance(source_id, str) or not source_id:
                raise StoppingPolicyError(f"run-log event {index}: source event requires source_id")
            current["sources"].add(source_id)
        elif event_type in ANSWER_EVENTS:
            current["answers"] += _positive_count(event.get("count", 1), f"run-log event {index} count", allow_zero=False)
        elif event_type == "EVIDENCE_ROUND":
            new_evidence = _positive_count(event.get("new_evidence_count"), f"run-log event {index} new_evidence_count")
            current["saturation"] = current["saturation"] + 1 if new_evidence == 0 else 0
        elif event_type == "SEARCH_FAILED":
            failure_key = event.get("failure_key", event.get("failure_class"))
            if not isinstance(failure_key, str) or not failure_key:
                raise StoppingPolicyError(f"run-log event {index}: failed search requires failure_key")
            current["failures"][failure_key] += 1
    return {
        question_id: QuestionMetrics(
            answers=values[question_id]["answers"],
            search_strategies=frozenset(values[question_id]["strategies"]),
            sources_reviewed=frozenset(values[question_id]["sources"]),
            saturation_rounds_without_new_evidence=values[question_id]["saturation"],
            failure_counts=dict(values[question_id]["failures"]),
        )
        for question_id in sorted(question_set)
    }


def _coerce_metrics(metrics: QuestionMetrics | Mapping[str, Any]) -> QuestionMetrics:
    if isinstance(metrics, QuestionMetrics):
        return metrics
    if not isinstance(metrics, Mapping):
        raise StoppingPolicyError("question metrics must be a mapping or QuestionMetrics")
    strategies = metrics.get("search_strategies", [])
    sources = metrics.get("sources_reviewed", [])
    failures = metrics.get("failure_counts", {})
    if not isinstance(strategies, (list, tuple, set, frozenset)) or any(not isinstance(value, str) for value in strategies):
        raise StoppingPolicyError("metrics.search_strategies must be a collection of strings")
    if not isinstance(sources, (list, tuple, set, frozenset)) or any(not isinstance(value, str) for value in sources):
        raise StoppingPolicyError("metrics.sources_reviewed must be a collection of strings")
    if not isinstance(failures, Mapping):
        raise StoppingPolicyError("metrics.failure_counts must be a mapping")
    failure_counts = {str(key): _positive_count(value, f"metrics.failure_counts[{key!r}]") for key, value in failures.items()}
    return QuestionMetrics(
        answers=_positive_count(metrics.get("answers", 0), "metrics.answers"),
        search_strategies=frozenset(strategies),
        sources_reviewed=frozenset(sources),
        saturation_rounds_without_new_evidence=_positive_count(
            metrics.get("saturation_rounds_without_new_evidence", 0), "metrics.saturation_rounds_without_new_evidence"
        ),
        failure_counts=failure_counts,
    )


def evaluate_question(
    question: Mapping[str, Any], metrics: QuestionMetrics | Mapping[str, Any], policy: StoppingPolicy
) -> dict[str, Any]:
    question_id = question.get("id")
    if not isinstance(question_id, str) or not question_id:
        raise StoppingPolicyError("question requires a non-empty id")
    current_status = question.get("status", QUESTION_OPEN_STATUS)
    if not isinstance(current_status, str):
        raise StoppingPolicyError(f"question {question_id}: status must be a string")
    if current_status in policy.terminal_question_statuses:
        return {
            "question_id": question_id,
            "from_status": current_status,
            "status": current_status,
            "terminal": True,
            "reason": "already_terminal",
            "metrics": _coerce_metrics(metrics).as_dict(),
            "limits": policy.limits(question),
        }
    if current_status != QUESTION_OPEN_STATUS:
        raise StoppingPolicyError(f"question {question_id}: unsupported non-terminal status {current_status!r}")
    normalized = _coerce_metrics(metrics)
    limits = policy.limits(question)
    status = QUESTION_OPEN_STATUS
    reason = "within_bounds"
    if normalized.answers >= limits["sufficient_answers"]:
        status, reason = "ANSWERED", "sufficient_answers"
    else:
        exhausted_failure = sorted(
            failure_key
            for failure_key, count in normalized.failure_counts.items()
            if count >= limits["max_identical_failure_retries"]
        )
        if exhausted_failure:
            status, reason = "BLOCKED", f"identical_failure_limit:{exhausted_failure[0]}"
        elif len(normalized.search_strategies) >= limits["max_search_strategies"]:
            status, reason = "UNRESOLVED", "search_strategy_limit"
        elif len(normalized.sources_reviewed) >= limits["max_sources_reviewed"]:
            status, reason = "UNRESOLVED", "source_review_limit"
        elif normalized.saturation_rounds_without_new_evidence >= limits["saturation_rounds_without_new_evidence"]:
            status, reason = "UNRESOLVED", "evidence_saturation"
    return {
        "question_id": question_id,
        "from_status": current_status,
        "status": status,
        "terminal": status in policy.terminal_question_statuses,
        "reason": reason,
        "metrics": normalized.as_dict(),
        "limits": limits,
    }


def evaluate_project(root: Path, target: str) -> dict[str, Any]:
    project = _project(root.resolve(), target)
    policy = StoppingPolicy.from_root(root.resolve())
    questions = yaml_list(project / "01_planning" / "question-register.yaml", "questions")
    question_ids = [question.get("id") for question in questions]
    if any(not isinstance(question_id, str) for question_id in question_ids):
        raise StoppingPolicyError("every question must have a string id")
    if len(set(question_ids)) != len(question_ids):
        raise StoppingPolicyError("question IDs must be unique for stopping evaluation")
    metrics = metrics_from_events(read_jsonl(project / "07_runtime" / "run-log.jsonl"), question_ids)
    decisions = [evaluate_question(question, metrics[question["id"]], policy) for question in questions]
    return {
        "project_id": load_json(project / "07_runtime" / "research-state.json").get("project_id"),
        "terminal": all(decision["terminal"] for decision in decisions),
        "decisions": decisions,
    }


def _next_event_id(events: list[dict[str, Any]]) -> str:
    used = {event.get("event_id", event.get("id")) for event in events}
    number = 1
    while f"STOP-EVT-{number:06d}" in used:
        number += 1
    return f"STOP-EVT-{number:06d}"


def apply_project(root: Path, target: str, *, evaluated_at: str) -> dict[str, Any]:
    project = _project(root.resolve(), target)
    evaluated_at = _validate_timestamp(evaluated_at)
    policy = StoppingPolicy.from_root(root.resolve())
    evaluation = evaluate_project(root.resolve(), target)
    changed = [decision for decision in evaluation["decisions"] if decision["status"] != decision["from_status"]]
    if not changed:
        return {**evaluation, "changed": []}
    questions_path = project / "01_planning" / "question-register.yaml"
    state_path = project / "07_runtime" / "research-state.json"
    log_path = project / "07_runtime" / "run-log.jsonl"
    questions_before = questions_path.read_text(encoding="utf-8")
    state_before = state_path.read_text(encoding="utf-8")
    log_before = log_path.read_text(encoding="utf-8")
    try:
        questions_document = load_yaml(questions_path) or {}
        state = load_json(state_path)
        events = read_jsonl(log_path)
        records = questions_document.get("questions") if isinstance(questions_document, dict) else None
        if not isinstance(records, list):
            raise StoppingPolicyError(f"{questions_path}: questions must be a list")
        by_id = {record["id"]: record for record in records if isinstance(record, dict) and isinstance(record.get("id"), str)}
        generated: list[dict[str, Any]] = []
        for decision in changed:
            record = by_id.get(decision["question_id"])
            if record is None:
                raise StoppingPolicyError(f"question disappeared during stopping evaluation: {decision['question_id']}")
            record["status"] = decision["status"]
            generated.append(
                {
                    "event_id": _next_event_id(events + generated),
                    "event_type": "STOPPING_DECISION",
                    "occurred_at": evaluated_at,
                    "question_id": decision["question_id"],
                    "question_from_status": decision["from_status"],
                    "question_to_status": decision["status"],
                    "reason": decision["reason"],
                    "metrics": decision["metrics"],
                }
            )
        all_terminal = all(record.get("status") in policy.terminal_question_statuses for record in records)
        state["updated_at"] = evaluated_at
        state["last_event_id"] = generated[-1]["event_id"]
        state["resume_from"] = (
            "All questions are terminal; run validation and completion checks."
            if all_terminal
            else "Continue the next OPEN question within its configured stopping bounds."
        )
        atomic_write_text(questions_path, yaml.safe_dump(questions_document, sort_keys=False, allow_unicode=True))
        atomic_write_text(state_path, stable_json(state))
        atomic_write_text(log_path, log_before + "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in generated))
    except Exception:
        atomic_write_text(questions_path, questions_before)
        atomic_write_text(state_path, state_before)
        atomic_write_text(log_path, log_before)
        raise
    return {**evaluation, "terminal": all_terminal, "changed": generated}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate and apply bounded research stopping rules.")
    parser.add_argument("target")
    parser.add_argument("command", choices=["evaluate", "apply"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--evaluated-at")
    args = parser.parse_args()
    try:
        if args.command == "evaluate":
            result = evaluate_project(args.root.resolve(), args.target)
        else:
            if not args.evaluated_at:
                parser.error("apply requires --evaluated-at for deterministic output")
            result = apply_project(args.root.resolve(), args.target, evaluated_at=args.evaluated_at)
    except (FileNotFoundError, StoppingPolicyError, ValueError) as exc:
        parser.error(str(exc))
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
