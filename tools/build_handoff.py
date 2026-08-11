from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, load_yaml
from canonical import handoff_hash_payload, handoff_sha256
from handoff_common import HandoffInputError, HandoffSources, load_handoff_sources, yaml_text
from validate import validate_repository


HANDOFF_ID_PATTERN = re.compile(r"^HO[0-9]{3,}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_OWNERS = {"research", "production", "human", "external"}
REJECTED_HYPOTHESIS_STATUSES = {"REJECTED", "CANCELLED", "FAILED"}
REJECTED_HYPOTHESIS_RECOMMENDATIONS = {"REJECTED", "ON_HOLD"}
INACTIVE_PLAN_STATUSES = {"CANCELLED", "FAILED"}


class HandoffBuildError(ValueError):
    """Raised when a handoff cannot be generated without guessing."""


def _records_by_id(records: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise HandoffBuildError(f"{label}[{index}].id must be a non-empty string")
        if record_id in result:
            raise HandoffBuildError(f"{label}: duplicate ID {record_id!r}")
        result[record_id] = record
    return result


def _unique_strings(value: Any, *, field: str, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise HandoffBuildError(f"{field} must be a list of strings")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise HandoffBuildError(f"{field}[{index}] must be a non-empty string")
        if item not in seen:
            result.append(item)
            seen.add(item)
    if required and not result:
        raise HandoffBuildError(f"{field} must contain at least one item")
    return result


def _source_refs(sources: HandoffSources, candidate_ids: set[str]) -> dict[str, list[str]]:
    hypotheses = _records_by_id(sources.hypotheses, "hypotheses")
    decisions = _records_by_id(sources.decisions, "decisions")
    insights = _records_by_id(sources.insights, "insights")
    claims = _records_by_id(sources.claims, "claims")
    decision_ids: set[str] = set()
    insight_ids: set[str] = set()
    evidence_ids: set[str] = set()

    for hypothesis_id in sorted(candidate_ids):
        hypothesis = hypotheses.get(hypothesis_id)
        if hypothesis is None:
            raise HandoffBuildError(f"hypothesis {hypothesis_id!r} is missing from production-hypotheses.yaml")
        for decision_id in _unique_strings(
            hypothesis.get("source_decision_ids"),
            field=f"hypotheses[{hypothesis_id}].source_decision_ids",
            required=True,
        ):
            decision_ids.add(decision_id)
        for insight_id in _unique_strings(
            hypothesis.get("source_insight_ids"),
            field=f"hypotheses[{hypothesis_id}].source_insight_ids",
            required=True,
        ):
            insight_ids.add(insight_id)

    for decision_id in sorted(decision_ids):
        decision = decisions.get(decision_id)
        if decision is None:
            raise HandoffBuildError(f"decision {decision_id!r} is missing from decision-log.yaml")
        for evidence_id in _unique_strings(
            decision.get("evidence_ids", []),
            field=f"decisions[{decision_id}].evidence_ids",
        ):
            evidence_ids.add(evidence_id)

    for insight_id in sorted(insight_ids):
        insight = insights.get(insight_id)
        if insight is None:
            raise HandoffBuildError(f"insight {insight_id!r} is missing from insight-register.yaml")
        for claim_id in _unique_strings(
            insight.get("claim_ids", []),
            field=f"insights[{insight_id}].claim_ids",
        ):
            claim = claims.get(claim_id)
            if claim is None:
                raise HandoffBuildError(f"claim {claim_id!r} is missing from claims.jsonl")
            for evidence_id in _unique_strings(
                claim.get("evidence_ids", []),
                field=f"claims[{claim_id}].evidence_ids",
            ):
                evidence_ids.add(evidence_id)

    evidence = _records_by_id(sources.evidence, "evidence")
    missing_evidence = sorted(evidence_ids - set(evidence))
    if missing_evidence:
        raise HandoffBuildError(f"evidence IDs are missing from evidence-ledger.jsonl: {', '.join(missing_evidence)}")
    return {
        "decision_ids": sorted(decision_ids),
        "insight_ids": sorted(insight_ids),
        "evidence_ids": sorted(evidence_ids),
    }


def _active_hypotheses(sources: HandoffSources) -> list[dict[str, Any]]:
    active = [
        hypothesis
        for hypothesis in sources.hypotheses
        if hypothesis.get("status") not in REJECTED_HYPOTHESIS_STATUSES
        and hypothesis.get("recommendation") not in REJECTED_HYPOTHESIS_RECOMMENDATIONS
    ]
    if not active:
        raise HandoffBuildError("production-hypotheses.yaml has no active production hypothesis candidate")
    return sorted(active, key=lambda record: str(record.get("id", "")))


def _selection(sources: HandoffSources) -> tuple[str | None, list[str], str, str, bool, set[str]]:
    active = _active_hypotheses(sources)
    active_ids = {str(record["id"]) for record in active}
    selected: str | None = None
    complete_recommendations = {
        comparison.get("recommended_hypothesis_id")
        for comparison in sources.comparisons
        if comparison.get("status") == "COMPLETE"
        and isinstance(comparison.get("recommended_hypothesis_id"), str)
        and comparison.get("recommended_hypothesis_id") in active_ids
    }
    if len(complete_recommendations) == 1:
        selected = next(iter(complete_recommendations))
    elif len(active) == 1:
        selected = str(active[0]["id"])

    alternatives = sorted(active_ids - ({selected} if selected else set()))
    if selected:
        return selected, alternatives, "AGENT_RECOMMENDED", "agent-recommended", False, active_ids
    return None, alternatives, "HUMAN_SELECTION_REQUIRED", "policy-derived", True, active_ids


def _gap_id(raw_id: Any, index: int, used: set[str]) -> str:
    if isinstance(raw_id, str) and re.fullmatch(r"GP[0-9]{3,}", raw_id):
        candidate = raw_id
    else:
        digits = re.search(r"([0-9]+)$", str(raw_id or ""))
        candidate = f"GP{int(digits.group(1)):03d}" if digits else f"GP{index + 1:03d}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    next_index = index + 1
    while f"GP{next_index:03d}" in used:
        next_index += 1
    candidate = f"GP{next_index:03d}"
    used.add(candidate)
    return candidate


def _open_gaps(sources: HandoffSources) -> list[dict[str, Any]]:
    report = sources.completion_report
    gaps = report.get("gaps", [])
    blockers = report.get("blockers", [])
    if not isinstance(gaps, list) or not isinstance(blockers, list):
        raise HandoffBuildError("completion-report.json gaps and blockers must be lists")
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            raise HandoffBuildError(f"completion-report.json#gaps[{index}] must be an object")
        statement = gap.get("reason")
        impact = gap.get("impact")
        if not isinstance(statement, str) or not statement.strip() or not isinstance(impact, str) or not impact.strip():
            raise HandoffBuildError(f"completion-report.json#gaps[{index}] needs non-empty reason and impact")
        result.append(
            {
                "id": _gap_id(gap.get("id"), index, used),
                "statement": statement,
                "impact": impact,
                "resolution_owner": gap.get("resolution_owner", "production")
                if gap.get("resolution_owner") in ALLOWED_OWNERS
                else "production",
                "blocking": gap.get("blocking") is True,
            }
        )
    for index, blocker in enumerate(blockers, start=len(result)):
        if not isinstance(blocker, dict):
            raise HandoffBuildError(f"completion-report.json#blockers[{index - len(gaps)}] must be an object")
        statement = blocker.get("reason")
        impact = blocker.get("release_condition")
        if not isinstance(statement, str) or not statement.strip() or not isinstance(impact, str) or not impact.strip():
            raise HandoffBuildError(f"completion-report.json#blockers[{index - len(gaps)}] needs reason and release_condition")
        result.append(
            {
                "id": _gap_id(blocker.get("id"), index, used),
                "statement": statement,
                "impact": impact,
                "resolution_owner": blocker.get("resolution_owner", "research")
                if blocker.get("resolution_owner") in ALLOWED_OWNERS
                else "research",
                "blocking": True,
            }
        )
    return sorted(result, key=lambda record: record["id"])


def _active_requirements(sources: HandoffSources) -> list[dict[str, Any]]:
    records = [
        requirement
        for requirement in sources.requirements
        if requirement.get("status") not in {"REJECTED", "INVALIDATED"}
    ]
    if not records:
        raise HandoffBuildError("production-requirements.yaml has no active requirement")
    acceptance_ids = _records_by_id(sources.acceptance_tests, "acceptance_tests")
    result: list[dict[str, Any]] = []
    for requirement in sorted(records, key=lambda record: str(record.get("id", ""))):
        requirement_id = requirement.get("id")
        if not isinstance(requirement_id, str):
            raise HandoffBuildError("every active requirement must have an ID")
        decisions = sorted(_unique_strings(requirement.get("source_decisions"), field=f"requirements[{requirement_id}].source_decisions"))
        tests = sorted(_unique_strings(requirement.get("acceptance_test_ids"), field=f"requirements[{requirement_id}].acceptance_test_ids"))
        missing_tests = sorted(test_id for test_id in tests if test_id not in acceptance_ids)
        if missing_tests:
            raise HandoffBuildError(f"requirement {requirement_id!r} references missing acceptance tests: {', '.join(missing_tests)}")
        result.append(
            {
                "id": requirement_id,
                "statement": requirement.get("statement"),
                "priority": requirement.get("priority"),
                "source_decision_ids": decisions,
                "acceptance_test_ids": tests,
            }
        )
    return result


def _constraints(sources: HandoffSources) -> dict[str, list[str]]:
    source = sources.constraints
    rights = _unique_strings(source.get("rights", []), field="constraints.rights")
    safety = _unique_strings(source.get("safety", []), field="constraints.safety")
    privacy = _unique_strings(source.get("privacy", []), field="constraints.privacy") if "privacy" in source else []
    prohibited = _unique_strings(source.get("prohibited_actions", []), field="constraints.prohibited_actions", required=True)
    return {"rights": rights, "safety": safety, "privacy": privacy, "prohibited_actions": prohibited}


def _git_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HandoffBuildError("research_commit is required when the root is not a Git worktree") from exc
    commit = result.stdout.strip()
    if not SHA_PATTERN.fullmatch(commit):
        raise HandoffBuildError("git rev-parse HEAD did not return a 40-character lowercase SHA")
    return commit


def _policy(sources: HandoffSources) -> dict[str, Any]:
    path = sources.root / "config" / "handoff-policy.yaml"
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise HandoffBuildError(f"{path}: handoff policy must be a mapping")
    return value


def build_handoff_payload(
    sources: HandoffSources,
    *,
    generated_at: str | None = None,
    research_commit: str | None = None,
    handoff_id: str | None = None,
    revision: int | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    policy = _policy(sources)
    previous = sources.handoff if sources.handoff.get("handoff_id") else {}
    selected, alternatives, selection_status, authority, human_required, candidate_ids = _selection(sources)
    source_refs = _source_refs(sources, candidate_ids)
    requirements = _active_requirements(sources)
    plans = _records_by_id(sources.prototype_plans, "prototype_plans")
    active_plan_ids = sorted(
        plan_id
        for plan_id, plan in plans.items()
        if plan.get("hypothesis_id") in candidate_ids and plan.get("status") not in INACTIVE_PLAN_STATUSES
    )
    constraints = _constraints(sources)
    open_gaps = _open_gaps(sources)
    triggers = _unique_strings(
        sources.completion_report.get("reopen_triggers", []),
        field="completion-report.json#reopen_triggers",
        required=True,
    )

    final_id = handoff_id or previous.get("handoff_id") or "HO001"
    if not isinstance(final_id, str) or not HANDOFF_ID_PATTERN.fullmatch(final_id):
        raise HandoffBuildError("handoff_id must match HO followed by at least three digits")
    final_revision = revision if revision is not None else previous.get("revision", 1)
    if type(final_revision) is not int or final_revision < 1:
        raise HandoffBuildError("revision must be a positive integer")
    final_supersedes = supersedes if supersedes is not None else previous.get("supersedes")
    if final_supersedes is not None and (not isinstance(final_supersedes, str) or not HANDOFF_ID_PATTERN.fullmatch(final_supersedes)):
        raise HandoffBuildError("supersedes must be a handoff ID or null")
    if final_supersedes == final_id:
        raise HandoffBuildError("supersedes cannot be the new handoff ID")

    commit = research_commit or _git_head(sources.root)
    if not isinstance(commit, str) or not SHA_PATTERN.fullmatch(commit):
        raise HandoffBuildError("research_commit must be a 40-character lowercase Git SHA")
    timestamp = generated_at or previous.get("generated_at") or sources.project_data.get("updated_at") or sources.project_data.get("created_at")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise HandoffBuildError("generated_at must be supplied or resolved from the existing handoff or manifest metadata")

    project_status = sources.project_data.get("status")
    plan_blocked = any(plans[plan_id].get("status") in {"BLOCKED", "FAILED"} for plan_id in active_plan_ids)
    # Keep the readiness expression explicit: a DRAFT handoff is safer than
    # claiming that an unresolved lifecycle or prototype blocker is ready.
    ready = (
        selected is not None
        and project_status not in {"BLOCKED", "CANCELLED"}
        and not any(gap["blocking"] for gap in open_gaps)
        and not plan_blocked
    )
    handoff: dict[str, Any] = {
        "schema_version": policy.get("handoff_schema_version", "1.0.0"),
        "handoff_id": final_id,
        "revision": final_revision,
        "status": "READY" if ready else "DRAFT",
        "supersedes": final_supersedes,
        "research_project_id": sources.project_id,
        "research_project_version": sources.project_data.get("version"),
        "research_commit": commit,
        "generated_at": timestamp,
        "selection": {
            "status": selection_status,
            "selected_hypothesis_id": selected,
            "alternative_hypothesis_ids": alternatives,
            "authority": authority,
            "human_approval_required": human_required,
        },
        "creative_direction_ref": "05_production/creative-direction.md",
        "requirements": requirements,
        "prototype_plan_ids": active_plan_ids,
        "constraints": constraints,
        "open_gaps": open_gaps,
        "replan_triggers": triggers,
        "source_refs": source_refs,
    }
    handoff["integrity"] = {"content_sha256": handoff_sha256(handoff)}

    if previous:
        previous_payload = handoff_hash_payload(previous)
        new_payload = handoff_hash_payload(handoff)
        if previous_payload != new_payload:
            previous_id = previous.get("handoff_id")
            previous_revision = previous.get("revision")
            if final_id == previous_id and final_revision == previous_revision:
                raise HandoffBuildError(
                    "existing handoff would change in place; provide a new --handoff-id and --revision with --supersedes"
                )
            if not final_supersedes:
                raise HandoffBuildError(
                    "changed handoff requires --supersedes pointing to the previous handoff ID"
                )
            if type(previous_revision) is int and final_revision <= previous_revision:
                raise HandoffBuildError("a changed handoff must increment revision")
    return handoff


def build_handoff(
    root: Path,
    target: str,
    *,
    generated_at: str | None = None,
    research_commit: str | None = None,
    handoff_id: str | None = None,
    revision: int | None = None,
    supersedes: str | None = None,
) -> Path:
    sources = load_handoff_sources(root, target)
    payload = build_handoff_payload(
        sources,
        generated_at=generated_at,
        research_commit=research_commit,
        handoff_id=handoff_id,
        revision=revision,
        supersedes=supersedes,
    )
    path = sources.project / "05_production" / "production-handoff.yaml"
    existed = path.exists()
    previous_text = path.read_text(encoding="utf-8") if existed else None
    atomic_write_text(path, yaml_text(payload))
    findings = validate_repository(sources.root, f"project/{sources.project.name}")
    if findings:
        if previous_text is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, previous_text)
        rendered = "\n".join(finding.render() for finding in findings)
        raise HandoffBuildError(f"generated handoff failed repository validation:\n{rendered}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic production handoff from canonical project sources.")
    parser.add_argument("target", help="project/<slug>")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--generated-at", help="RFC 3339 timestamp; defaults to existing handoff or manifest metadata")
    parser.add_argument("--research-commit", help="40-character source Git SHA; defaults to HEAD")
    parser.add_argument("--handoff-id")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--supersedes")
    args = parser.parse_args()
    try:
        path = build_handoff(
            args.root.resolve(),
            args.target,
            generated_at=args.generated_at,
            research_commit=args.research_commit,
            handoff_id=args.handoff_id,
            revision=args.revision,
            supersedes=args.supersedes,
        )
    except (HandoffInputError, HandoffBuildError, OSError) as exc:
        parser.error(str(exc))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
