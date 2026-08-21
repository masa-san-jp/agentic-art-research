from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _common import load_yaml


MINIMUM_KEYS = ("evidence", "claims", "insights", "decisions", "requirements")
REQUIRED_RECORD_KEYS = ("rejected_options", "uncertainty", "prior_art", "self_repetition_review")
REQUIRED_RECORD_LABELS = {
    "rejected_options": "04_decisions/rejected-options.yaml",
    "uncertainty": "04_decisions/uncertainty-register.yaml",
    "prior_art": "03_knowledge/prior-art.jsonl",
    "self_repetition_review": "04_decisions/self-repetition-review.yaml",
}


class CompletionQualityConfigError(ValueError):
    """Raised when the completion-quality policy cannot be interpreted safely."""


@dataclass(frozen=True)
class CompletionQualityPolicy:
    minimums: dict[str, int]
    required_records: tuple[str, ...]


def load_completion_quality_policy(root: Path, research_plan: Any) -> CompletionQualityPolicy:
    policy_path = root / "config" / "stopping-policy.yaml"
    policy = load_yaml(policy_path)
    if not isinstance(policy, dict) or not isinstance(policy.get("defaults"), dict):
        raise CompletionQualityConfigError(f"{policy_path}: defaults must be a mapping")
    defaults = policy["defaults"]
    raw_minimums = defaults.get("completion_minimums")
    if not isinstance(raw_minimums, dict):
        raise CompletionQualityConfigError(f"{policy_path}: defaults.completion_minimums must be a mapping")
    default_minimums: dict[str, int] = {}
    for key in MINIMUM_KEYS:
        value = raw_minimums.get(key)
        if type(value) is not int or value < 0:
            raise CompletionQualityConfigError(
                f"{policy_path}: defaults.completion_minimums.{key} must be a non-negative integer"
            )
        default_minimums[key] = value

    required_records = defaults.get("completion_required_records")
    if not isinstance(required_records, list) or any(item not in REQUIRED_RECORD_KEYS for item in required_records):
        raise CompletionQualityConfigError(
            f"{policy_path}: defaults.completion_required_records must contain known record keys"
        )

    overrides = research_plan.get("minimums") if isinstance(research_plan, dict) else None
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise CompletionQualityConfigError("01_planning/research-plan.yaml: minimums must be a mapping")
    unknown = sorted(set(overrides) - set(MINIMUM_KEYS) - {"reason"})
    if unknown:
        raise CompletionQualityConfigError(
            "01_planning/research-plan.yaml: minimums contains unknown keys: " + ", ".join(unknown)
        )
    reason = overrides.get("reason")
    minimums = dict(default_minimums)
    lowered = False
    for key in MINIMUM_KEYS:
        if key not in overrides:
            continue
        value = overrides[key]
        if type(value) is not int or value < 0:
            raise CompletionQualityConfigError(
                f"01_planning/research-plan.yaml: minimums.{key} must be a non-negative integer"
            )
        minimums[key] = value
        lowered = lowered or value < default_minimums[key]
    if lowered and (not isinstance(reason, str) or not reason.strip()):
        raise CompletionQualityConfigError(
            "01_planning/research-plan.yaml: minimums.reason is required when lowering a default minimum"
        )
    return CompletionQualityPolicy(minimums=minimums, required_records=tuple(required_records))


def quality_failures(policy: CompletionQualityPolicy, counts: dict[str, int]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for key, minimum in policy.minimums.items():
        actual = counts.get(key, 0)
        if actual < minimum:
            failures.append(
                {
                    "id": f"QUALITY-MINIMUM-{key.upper()}",
                    "reason": f"{key} count {actual} is below the minimum {minimum}.",
                    "impact": "The project is under-researched and cannot reach production handoff.",
                }
            )
    for key in policy.required_records:
        if counts.get(key, 0) < 1:
            failures.append(
                {
                    "id": f"QUALITY-REQUIRED-{key.upper()}",
                    "reason": f"At least one record is required in {REQUIRED_RECORD_LABELS[key]}.",
                    "impact": "The project lacks a required research-quality review record.",
                }
            )
    return failures
