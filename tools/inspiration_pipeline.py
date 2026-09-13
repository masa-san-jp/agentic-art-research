"""Provider-neutral inspiration candidate pipeline.

An agent may propose many creative directions, but only a typed, sourced and
feasible candidate can cross the Research/Production boundary.  This module
keeps that decision deterministic: it validates agent output, detects
semantic duplicates, critiques the concrete proposal, compares survivors,
and renders the existing production handoff records.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import atomic_write_text, load_json, load_yaml, stable_json


CONTRACT_VERSION = "inspiration-pipeline/v1"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ID_NUMBER = re.compile(r"([0-9]+)$")
IGNORED_SEMANTIC_KEYS = {"id", "title", "device_name", "quantity", "revision", "status", "recommendation", "knowledge_refs"}
EXPERIMENT_MARKERS = {
    "viewer", "move", "movement", "enter", "turn", "notice", "discover", "shadow", "light",
    "line", "grid", "surface", "wall", "floor", "sound", "sheet", "frame", "interval",
    "material", "repeat", "omit", "hang", "place", "path", "space", "distance", "cm", "meter",
}
EXPERIENCE_MARKERS = {
    "experience", "viewer", "notice", "discover", "feel", "encounter", "perceive", "attention",
    "meaning", "relation", "tension", "memory", "silence", "presence", "absence",
}
MEASUREMENT_MARKERS = {"cm", "mm", "meter", "metre", "width", "height", "depth", "dimension", "distance", "quantity", "count"}
UNSAFE_SOURCE_MARKERS = ("example.invalid", "example.com", "localhost", "fixture", "fixtures/", "file://")


class InspirationPipelineError(ValueError):
    """Raised when an inspiration packet cannot safely cross the boundary."""


def _schema_validator(name: str) -> Draft202012Validator:
    root = Path(__file__).resolve().parents[1] / "schemas"
    schema = load_json(root / f"{name}.schema.json")
    common = load_json(root / "common.schema.json")
    registry = Registry().with_resource(common["$id"], Resource.from_contents(common))
    validator = Draft202012Validator(schema, registry=registry)
    validator.check_schema(schema)
    return validator


def _schema_validate(value: Any, name: str, label: str) -> None:
    errors = sorted(_schema_validator(name).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.path) or "$"
        raise InspirationPipelineError(f"INSP-SCHEMA: {label}.{path}: {error.message}")


def _semantic_value(value: Any, *, top_level: bool = True) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            if key in IGNORED_SEMANTIC_KEYS:
                continue
            result[key] = _semantic_value(value[key], top_level=False)
        return result
    if isinstance(value, list):
        return [_semantic_value(item, top_level=False) for item in value]
    return value


def semantic_fingerprint(candidate: dict[str, Any]) -> str:
    """Hash the substantive candidate fields.

    Candidate identity, revision, labels, device names, quantities and
    recommendation state are bookkeeping.  Composition, experience,
    material relation, cited transformation and completion path remain part
    of the fingerprint.
    """

    payload = json.dumps(_semantic_value(candidate), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _candidate_text(candidate: dict[str, Any]) -> str:
    def collect(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for child in value.values() for item in collect(child)]
        if isinstance(value, list):
            return [item for child in value for item in collect(child)]
        return []

    return " ".join(collect(candidate)).casefold()


def _has_concrete_composition(candidate: dict[str, Any]) -> bool:
    composition = candidate.get("composition")
    text = " ".join(item.casefold() for item in _strings(composition))
    return any(marker in text.split() or marker in text for marker in EXPERIMENT_MARKERS)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    return []


def _validate_source_refs(candidate: dict[str, Any]) -> None:
    transformation = candidate["input_transformation"]
    if transformation.get("epistemic_status") != "CREATIVE_PROPOSAL":
        raise InspirationPipelineError(
            "INSP-CREATIVE-STATUS: input_transformation.creative_leap must remain CREATIVE_PROPOSAL"
        )
    for index, source in enumerate(transformation["source_refs"]):
        commit = source.get("source_commit")
        if not isinstance(commit, str) or not SHA_PATTERN.fullmatch(commit):
            raise InspirationPipelineError(
                f"INSP-SOURCE-COMMIT: input_transformation.source_refs[{index}].source_commit must be a lowercase 40-character SHA"
            )
        url = source.get("source_url")
        if isinstance(url, str):
            lowered = url.casefold()
            if any(marker in lowered for marker in UNSAFE_SOURCE_MARKERS):
                raise InspirationPipelineError(
                    f"INSP-SOURCE-URL: input_transformation.source_refs[{index}] is a fixture/example or local URL"
                )


def validate_candidate(candidate: dict[str, Any], *, label: str = "candidate") -> None:
    transformation = candidate.get("input_transformation")
    if isinstance(transformation, dict) and transformation.get("epistemic_status") not in (None, "CREATIVE_PROPOSAL"):
        raise InspirationPipelineError(
            "INSP-CREATIVE-STATUS: input_transformation.creative_leap must remain CREATIVE_PROPOSAL"
        )
    _schema_validate(candidate, "inspiration-candidate", label)
    _validate_source_refs(candidate)
    if not candidate["feasibility"]["completion_path"]:
        raise InspirationPipelineError(f"INSP-INCOMPLETE-PATH: {label}.feasibility.completion_path is empty")


def validate_candidates(candidates: list[dict[str, Any]]) -> None:
    seen_ids: set[str] = set()
    seen_fingerprints: dict[str, str] = {}
    for index, candidate in enumerate(candidates):
        validate_candidate(candidate, label=f"candidates[{index}]")
        candidate_id = candidate["id"]
        if candidate_id in seen_ids:
            raise InspirationPipelineError(f"INSP-DUPLICATE-ID: candidate ID {candidate_id!r} is repeated")
        seen_ids.add(candidate_id)
        fingerprint = semantic_fingerprint(candidate)
        previous = seen_fingerprints.get(fingerprint)
        if previous is not None:
            raise InspirationPipelineError(
                f"INSP-DUPLICATE-SEMANTIC: {candidate_id} is semantically identical to {previous}; "
                "title, device_name and quantity do not create an independent candidate"
            )
        seen_fingerprints[fingerprint] = candidate_id


def critique_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Critique concrete content while allowing experimental expression."""

    validate_candidate(candidate, label="candidate")
    findings: list[dict[str, str]] = []
    text = _candidate_text(candidate)
    composition = candidate.get("composition") if isinstance(candidate.get("composition"), dict) else {}
    material = candidate.get("material_relationship") if isinstance(candidate.get("material_relationship"), dict) else {}
    feasibility = candidate.get("feasibility") if isinstance(candidate.get("feasibility"), dict) else {}

    if not _has_concrete_composition(candidate):
        findings.append(
            {
                "code": "POETIC_ONLY",
                "severity": "MAJOR",
                "field": "composition",
                "statement": "The proposal names a mood or poetic effect without a concrete image, relation, or viewer action.",
                "required_action": "Specify observable elements, their relation, and what the viewer can do or perceive.",
            }
        )

    measurement_text = " ".join(_strings(composition) + _strings(feasibility))
    has_measurement = any(marker in measurement_text.casefold() for marker in MEASUREMENT_MARKERS)
    experience_text = " ".join(_strings(candidate.get("proposition")) + _strings(candidate.get("intended_experience")))
    if has_measurement and not any(marker in experience_text.casefold() for marker in EXPERIENCE_MARKERS):
        findings.append(
            {
                "code": "MEASUREMENT_ONLY",
                "severity": "MAJOR",
                "field": "proposition",
                "statement": "Dimensions or counts are present without a compositional or experiential reason.",
                "required_action": "State what the measured relation makes the viewer notice, feel, or understand.",
            }
        )

    relation_text = " ".join(_strings(material.get("behavior")) + _strings(material.get("relation")))
    if not relation_text.strip() or len(relation_text.split()) < 2:
        findings.append(
            {
                "code": "MATERIAL_DISCONNECTED",
                "severity": "MAJOR",
                "field": "material_relationship",
                "statement": "The material is named without describing how its behavior carries the proposition.",
                "required_action": "Connect a material behavior to the intended experience and composition.",
            }
        )

    path = feasibility.get("completion_path")
    if not isinstance(path, list) or not path or any(not isinstance(item, str) or not item.strip() for item in path):
        findings.append(
            {
                "code": "INCOMPLETE_PATH",
                "severity": "MAJOR",
                "field": "feasibility.completion_path",
                "statement": "The proposal has no complete path from inputs through production to acceptance evidence.",
                "required_action": "List materials, method, required steps, and the observable completion condition.",
            }
        )

    return {
        "id": f"ICT{_number(candidate['id']):03d}",
        "candidate_id": candidate["id"],
        "candidate_revision": candidate["revision"],
        "findings": findings,
        "status": "PASS" if not findings else "REVISION_REQUIRED",
    }


def _number(value: Any, fallback: int = 1) -> int:
    match = ID_NUMBER.search(str(value))
    return int(match.group(1)) if match else fallback


def _rating(candidate: dict[str, Any], axis: str) -> tuple[int, str]:
    critique = critique_candidate(candidate)
    if any(item["severity"] == "MAJOR" for item in critique["findings"]):
        return 1, "A major critique finding remains unresolved."
    if axis == "experience":
        experience_words = sum(len(item.split()) for item in candidate["intended_experience"])
        score = min(5, 3 + int(experience_words >= 12) + int(len(candidate["intended_experience"]) >= 2))
        return score, f"The proposal gives {len(candidate['intended_experience'])} intended viewer experience(s) tied to its proposition."
    if axis == "composition":
        score = 5 if len(candidate["composition"]["elements"]) >= 2 else 4
        return score, "The candidate specifies observable elements, spatial relation, and viewer action."
    score = 5 if len(candidate["feasibility"]["completion_path"]) >= 3 else 4
    return score, "The candidate connects method, inputs, steps, alternatives, and acceptance evidence."


def compare_candidates(
    candidates: list[dict[str, Any]],
    *,
    comparison_id: str = "ICMP001",
    knowledge_used_ids: list[str] | None = None,
) -> dict[str, Any]:
    validate_candidates(candidates)
    if len(candidates) < 2:
        return {
            "id": comparison_id,
            "candidate_ids": [candidate["id"] for candidate in candidates],
            "axes": [],
            "recommended_candidate_id": None,
            "reason": "At least two semantically distinct candidates are required before an autonomous comparison can recommend one.",
            "knowledge_used_ids": sorted(set(knowledge_used_ids or [])),
            "status": "HUMAN_SELECTION_REQUIRED",
        }

    axes: list[dict[str, Any]] = []
    totals = {candidate["id"]: 0 for candidate in candidates}
    for name, key in (("intended experience", "experience"), ("composition and material relation", "composition"), ("completion feasibility", "feasibility")):
        assessments = []
        for candidate in candidates:
            rating, statement = _rating(candidate, key)
            totals[candidate["id"]] += rating
            assessments.append({"candidate_id": candidate["id"], "statement": statement, "rating": rating})
        axes.append({"name": name, "assessments": assessments})

    ranking = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    top_score = ranking[0][1]
    tied = [candidate_id for candidate_id, score in ranking if score == top_score]
    major_critique = any(critique_candidate(candidate)["findings"] for candidate in candidates)
    if len(tied) != 1 or major_critique:
        recommendation = None
        status = "HUMAN_SELECTION_REQUIRED" if len(tied) != 1 else "INCOMPLETE"
        reason = (
            "No recommendation was fabricated: candidates remain tied on the declared axes."
            if len(tied) != 1
            else "A candidate has an unresolved major critique finding; revise it before handoff."
        )
    else:
        recommendation = tied[0]
        status = "COMPLETE"
        score_text = ", ".join(f"{candidate_id}={score}" for candidate_id, score in ranking)
        recommendation_record = next(candidate for candidate in candidates if candidate["id"] == recommendation)
        reason = (
            f"Recommend {recommendation} ({score_text}) because it gives the strongest connected path from "
            f"{recommendation_record['proposition']} through concrete composition and feasible completion."
        )
    result = {
        "id": comparison_id,
        "candidate_ids": [candidate["id"] for candidate in candidates],
        "axes": axes,
        "recommended_candidate_id": recommendation,
        "reason": reason,
        "knowledge_used_ids": sorted(set(knowledge_used_ids or [])),
        "status": status,
    }
    _schema_validate(result, "inspiration-comparison", "comparison")
    return result


def revise_candidate(candidate: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    revised = copy.deepcopy(candidate)
    old_fingerprint = semantic_fingerprint(revised)

    def merge(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    merge(revised, updates)
    if semantic_fingerprint(revised) == old_fingerprint:
        raise InspirationPipelineError(f"INSP-NO-REVISION: {candidate['id']} revision does not change substantive content")
    revised["revision"] = int(candidate["revision"]) + 1
    revised["status"] = "REVISED"
    validate_candidate(revised, label=f"revised {candidate['id']}")
    return revised


def _production_ids(candidate: dict[str, Any]) -> tuple[str, str]:
    number = _number(candidate["id"])
    return f"PH{number:03d}", f"PP{number:03d}"


def build_production_records(candidates: list[dict[str, Any]], comparison: dict[str, Any]) -> dict[str, Any]:
    if comparison.get("status") != "COMPLETE" or not comparison.get("recommended_candidate_id"):
        raise InspirationPipelineError("INSP-HANDOFF-INCOMPLETE: a complete comparison with one recommendation is required")
    candidate_by_id = {candidate["id"]: candidate for candidate in candidates}
    selected_id = comparison["recommended_candidate_id"]
    if selected_id not in candidate_by_id:
        raise InspirationPipelineError(f"INSP-HANDOFF-REFERENCE: comparison selects missing candidate {selected_id!r}")

    hypotheses: list[dict[str, Any]] = []
    prototype_plans: list[dict[str, Any]] = []
    candidate_to_hypothesis: dict[str, str] = {}
    for candidate in candidates:
        hypothesis_id, plan_id = _production_ids(candidate)
        candidate_to_hypothesis[candidate["id"]] = hypothesis_id
        feasibility = candidate["feasibility"]
        uncertainties = [
            {
                "id": item["id"],
                "statement": item["statement"],
                "severity": item["severity"],
                "prototype_plan_ids": [plan_id],
                "external_validation_reason": item["external_validation_reason"],
            }
            for item in candidate["uncertainties"]
        ]
        hypotheses.append(
            {
                "id": hypothesis_id,
                "title": candidate["title"],
                "proposition": candidate["proposition"],
                "source_decision_ids": candidate["source_decision_ids"],
                "source_insight_ids": candidate["source_insight_ids"],
                "intended_experience": candidate["intended_experience"],
                "includes": candidate.get("includes") or candidate["composition"]["elements"],
                "excludes": candidate.get("excludes", []),
                "differentiation": candidate["differentiation"],
                "feasibility": {
                    "technical": f"{feasibility['method']}; required skills: {', '.join(feasibility['skills'])}.",
                    "cost_band": feasibility["cost_band"],
                    "duration_band": feasibility["duration_band"],
                    "venue_dependency": "MEDIUM" if feasibility["venue"] else "UNKNOWN",
                    "rights_status": feasibility["rights_status"],
                    "safety_status": feasibility["safety_status"],
                },
                "uncertainties": uncertainties,
                "recommendation": "RECOMMENDED" if candidate["id"] == selected_id else "ALTERNATIVE",
                "single_hypothesis_rationale": None,
                "status": "ADOPTED" if candidate["id"] == selected_id else "PROPOSED",
            }
        )
        prototype_plans.append(
            {
                "id": plan_id,
                "hypothesis_id": hypothesis_id,
                "question": candidate["core_question"],
                "uncertainty_ids": [item["id"] for item in uncertainties],
                "method": feasibility["method"],
                "inputs": [
                    f"03_plan/production-plan.yaml dimensions: {feasibility['dimensions']}",
                    f"03_plan/production-plan.yaml materials: {', '.join(feasibility['materials'])}",
                    f"03_plan/production-plan.yaml quantity: {feasibility['quantity']} {feasibility['unit']}",
                    "Selected hypothesis composition and placement",
                ],
                "constraints": candidate.get("excludes", []) + [f"Venue: {feasibility['venue']}"],
                "tasks": [
                    {
                        "id": f"PT{_number(candidate['id']):03d}",
                        "title": "Resolve structured production inputs",
                        "depends_on": [],
                        "completion_condition": "Dimensions, materials, quantity, unit, and placement are resolved from the production plan.",
                        "effect_type": "READ_ONLY",
                    },
                    {
                        "id": f"PT{_number(candidate['id']) + 100:03d}",
                        "title": "Render the deterministic digital prototype",
                        "depends_on": [f"PT{_number(candidate['id']):03d}"],
                        "completion_condition": "A deterministic SVG preview is written with the resolved plan values and the selected composition.",
                        "effect_type": "REPOSITORY_WRITE",
                    },
                ],
                "acceptance_test_ids": candidate["acceptance_test_ids"],
                "expected_evidence": "deterministic-svg-image-output",
                "estimated_cost_band": feasibility["cost_band"],
                "estimated_duration_band": "HOURS" if feasibility["duration_band"] == "HOURS" else feasibility["duration_band"],
                "executor_capability": "digital-prototype-renderer",
                "external_validation_reason": None,
                "status": "PLANNED",
            }
        )

    hypothesis_comparison = {
        "id": f"HC{_number(comparison['id']):03d}",
        "hypothesis_ids": [candidate_to_hypothesis[item] for item in comparison["candidate_ids"]],
        "axes": [
            {
                "name": axis["name"],
                "assessments": [
                    {
                        "hypothesis_id": candidate_to_hypothesis[assessment["candidate_id"]],
                        "statement": assessment["statement"],
                        "rating": assessment["rating"],
                    }
                    for assessment in axis["assessments"]
                ],
            }
            for axis in comparison["axes"]
        ],
        "recommended_hypothesis_id": candidate_to_hypothesis[selected_id],
        "reason": comparison["reason"],
        "status": "COMPLETE",
    }
    selected = candidate_by_id[selected_id]
    feasibility = selected["feasibility"]
    creative_direction = "\n".join(
        [
            f"# {selected['title']} — Creative direction",
            "",
            "## 作品の核",
            selected["proposition"],
            "",
            "## 引き継ぐ観察と創造的変換",
            f"観察: {selected['input_transformation']['observation']}",
            f"創造的提案: {selected['input_transformation']['creative_leap']}（外部事実ではない）",
            "",
            "## 具体的な構成",
            f"要素: {', '.join(selected['composition']['elements'])}",
            f"空間関係: {selected['composition']['spatial_relation']}",
            f"鑑賞者の行為: {selected['composition']['viewer_action']}",
            f"素材の働き: {selected['material_relationship']['behavior']}。関係: {selected['material_relationship']['relation']}",
            "",
            "## 制作の完了経路",
            *[f"{index}. {step}" for index, step in enumerate(feasibility["completion_path"], 1)],
            "",
            "## 未解決事項",
            *[f"- {item['id']}: {item['statement']}" for item in selected["uncertainties"]],
            "",
            "## 引用元",
            *[f"- {item['kind']}:{item['id']} @ {item['source_commit']}" for item in selected["input_transformation"]["source_refs"]],
            "",
        ]
    )
    handoff_trace = verify_handoff_preservation(
        selected,
        next(item for item in hypotheses if item["id"] == candidate_to_hypothesis[selected_id]),
        next(item for item in prototype_plans if item["hypothesis_id"] == candidate_to_hypothesis[selected_id]),
        creative_direction,
    )
    for index, hypothesis in enumerate(hypotheses):
        _schema_validate(hypothesis, "production-hypothesis", f"production.hypotheses[{index}]")
    _schema_validate(hypothesis_comparison, "hypothesis-comparison", "production.comparison")
    for index, plan in enumerate(prototype_plans):
        _schema_validate(plan, "prototype-plan", f"production.prototype_plans[{index}]")
    return {
        "hypotheses": hypotheses,
        "comparison": hypothesis_comparison,
        "prototype_plans": prototype_plans,
        "creative_direction": creative_direction,
        "handoff_trace": handoff_trace,
    }


def verify_handoff_preservation(
    candidate: dict[str, Any],
    hypothesis: dict[str, Any],
    prototype_plan: dict[str, Any],
    creative_direction: str,
) -> dict[str, Any]:
    """Detect a Production handoff that silently became a different idea."""

    differences: list[str] = []
    if hypothesis.get("proposition") != candidate.get("proposition"):
        differences.append("proposition")
    if hypothesis.get("intended_experience") != candidate.get("intended_experience"):
        differences.append("intended_experience")
    expected_elements = set(candidate.get("composition", {}).get("elements", []))
    actual_elements = set(hypothesis.get("includes", []))
    if not expected_elements.issubset(actual_elements):
        differences.append("composition.elements")
    if prototype_plan.get("question") != candidate.get("core_question"):
        differences.append("prototype_plan.question")
    feasibility = candidate.get("feasibility", {})
    if prototype_plan.get("method") != feasibility.get("method"):
        differences.append("prototype_plan.method")
    for required_text in (
        candidate.get("proposition", ""),
        candidate.get("input_transformation", {}).get("creative_leap", ""),
    ):
        if required_text and required_text not in creative_direction:
            differences.append("creative_direction")
            break
    return {
        "candidate_id": candidate.get("id"),
        "candidate_fingerprint": semantic_fingerprint(candidate),
        "creative_direction_sha256": f"sha256:{hashlib.sha256(creative_direction.encode('utf-8')).hexdigest()}",
        "differences": differences,
        "status": "MATCH" if not differences else "REVISION_REQUIRED",
        "action": "preserve adopted candidate and regenerate the changed handoff fields" if differences else "Production may continue with the adopted direction",
    }


def _knowledge_ids(packet: dict[str, Any], candidates: list[dict[str, Any]], *, require: bool) -> list[str]:
    previous = packet.get("previous_knowledge", [])
    if previous is None:
        previous = []
    if not isinstance(previous, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in previous):
        raise InspirationPipelineError("INSP-KNOWLEDGE-SHAPE: previous_knowledge must be a list of objects with IDs")
    previous_ids = {item["id"] for item in previous}
    used = sorted({ref for candidate in candidates for ref in candidate.get("knowledge_refs", []) if ref in previous_ids})
    if require and previous_ids and not used:
        raise InspirationPipelineError(
            "INSP-KNOWLEDGE-RELOAD_REQUIRED: the next run supplied prior comparison/adoption knowledge but no candidate used it"
        )
    return used


def process_packet(packet: dict[str, Any], *, require_previous_knowledge: bool = False) -> dict[str, Any]:
    if not isinstance(packet, dict) or packet.get("contract_version") != CONTRACT_VERSION:
        raise InspirationPipelineError(f"INSP-CONTRACT: packet.contract_version must be {CONTRACT_VERSION!r}")
    if not isinstance(packet.get("project_id"), str) or not packet["project_id"].startswith("project/"):
        raise InspirationPipelineError("INSP-PROJECT-ID: packet.project_id must be project/<slug>")
    if not isinstance(packet.get("research_commit"), str) or not SHA_PATTERN.fullmatch(packet["research_commit"]):
        raise InspirationPipelineError("INSP-RESEARCH-COMMIT: packet.research_commit must be a lowercase 40-character SHA")
    candidates = packet.get("candidates")
    if not isinstance(candidates, list) or any(not isinstance(item, dict) for item in candidates):
        raise InspirationPipelineError("INSP-CANDIDATES-SHAPE: packet.candidates must be a list of objects")
    candidates = copy.deepcopy(candidates)
    validate_candidates(candidates)

    revisions = packet.get("revisions", {})
    if revisions is None:
        revisions = {}
    if not isinstance(revisions, dict):
        raise InspirationPipelineError("INSP-REVISION-SHAPE: packet.revisions must be a mapping")
    by_id = {candidate["id"]: candidate for candidate in candidates}
    for candidate_id, updates in revisions.items():
        if candidate_id not in by_id or not isinstance(updates, dict):
            raise InspirationPipelineError(f"INSP-REVISION-REFERENCE: invalid revision for {candidate_id!r}")
        by_id[candidate_id] = revise_candidate(by_id[candidate_id], updates)
    candidates = [by_id[candidate_id] for candidate_id in sorted(by_id)]
    validate_candidates(candidates)

    critiques = [critique_candidate(candidate) for candidate in candidates]
    knowledge_used_ids = _knowledge_ids(packet, candidates, require=require_previous_knowledge)
    comparison = compare_candidates(candidates, knowledge_used_ids=knowledge_used_ids)
    production = build_production_records(candidates, comparison) if comparison["status"] == "COMPLETE" else None
    return {
        "contract_version": CONTRACT_VERSION,
        "project_id": packet.get("project_id"),
        "research_commit": packet.get("research_commit"),
        "candidates": candidates,
        "candidate_fingerprints": {candidate["id"]: semantic_fingerprint(candidate) for candidate in candidates},
        "critiques": critiques,
        "comparison": comparison,
        "knowledge_used_ids": knowledge_used_ids,
        "handoff_ready": production is not None,
        "production": production,
    }


def _merge_collection(path: Path, key: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_value = load_yaml(path) if path.exists() else {}
    existing = existing_value.get(key, []) if isinstance(existing_value, dict) else []
    if not isinstance(existing, list):
        raise InspirationPipelineError(f"INSP-EXISTING-SHAPE: {path}#{key} must be a list")
    merged = {str(record.get("id")): record for record in existing if isinstance(record, dict) and isinstance(record.get("id"), str)}
    for record in records:
        record_id = str(record["id"])
        if record_id in merged and merged[record_id] != record:
            raise InspirationPipelineError(
                f"INSP-REVISION-REQUIRED: {path} already contains a different {record_id}; create a higher candidate revision"
            )
        merged[record_id] = record
    return [merged[key] for key in sorted(merged)]


def write_project_artifacts(project: Path, result: dict[str, Any]) -> list[Path]:
    """Apply a completed result to a project with idempotent, atomic writes."""

    production = result.get("production")
    if not result.get("handoff_ready") or not isinstance(production, dict):
        raise InspirationPipelineError("INSP-HANDOFF-INCOMPLETE: cannot write an incomplete result")
    project = project.resolve()
    if not project.is_dir() or not (project / "manifest.yaml").is_file():
        raise InspirationPipelineError("INSP-PROJECT: project must be an existing project directory")
    candidates_path = project / "04_decisions" / "inspiration-candidates.yaml"
    comparison_path = project / "04_decisions" / "inspiration-comparison.yaml"
    critiques_path = project / "04_decisions" / "inspiration-critiques.yaml"
    direction_path = project / "05_production" / "creative-direction.md"
    hypotheses_path = project / "04_decisions" / "production-hypotheses.yaml"
    hypothesis_comparison_path = project / "04_decisions" / "hypothesis-comparison.yaml"
    plans_path = project / "05_production" / "prototype-plans.yaml"

    candidate_records = _merge_collection(candidates_path, "candidates", result["candidates"])
    critique_records = _merge_collection(critiques_path, "critiques", result["critiques"])
    existing_comparison = load_yaml(comparison_path) if comparison_path.exists() else {}
    if isinstance(existing_comparison, dict):
        existing_comparisons = existing_comparison.get("comparisons", [])
        if not isinstance(existing_comparisons, list):
            raise InspirationPipelineError("INSP-EXISTING-SHAPE: inspiration comparison must contain a comparisons list")
        for previous in existing_comparisons:
            if isinstance(previous, dict) and previous.get("id") == result["comparison"].get("id") and previous != result["comparison"]:
                raise InspirationPipelineError("INSP-REVISION-REQUIRED: inspiration comparison would change in place")

    written: list[tuple[Path, str]] = [
        (candidates_path, yaml.safe_dump({"candidates": candidate_records}, sort_keys=False, allow_unicode=True)),
        (comparison_path, yaml.safe_dump({"comparisons": _merge_collection(comparison_path, "comparisons", [result["comparison"]])}, sort_keys=False, allow_unicode=True)),
        (critiques_path, yaml.safe_dump({"critiques": critique_records}, sort_keys=False, allow_unicode=True)),
        (direction_path, production["creative_direction"]),
        (hypotheses_path, yaml.safe_dump({"hypotheses": _merge_collection(hypotheses_path, "hypotheses", production["hypotheses"])}, sort_keys=False, allow_unicode=True)),
        (hypothesis_comparison_path, yaml.safe_dump({"comparisons": _merge_collection(hypothesis_comparison_path, "comparisons", [production["comparison"]])}, sort_keys=False, allow_unicode=True)),
        (plans_path, yaml.safe_dump({"prototype_plans": _merge_collection(plans_path, "prototype_plans", production["prototype_plans"])}, sort_keys=False, allow_unicode=True)),
    ]
    for path, content in written:
        atomic_write_text(path, content if content.endswith("\n") else content + "\n")
    return [path for path, _ in written]


def _load_packet(path: Path) -> dict[str, Any]:
    value = load_json(path) if path.suffix.lower() == ".json" else load_yaml(path)
    if not isinstance(value, dict):
        raise InspirationPipelineError("INSP-PACKET-SHAPE: input packet must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and route provider-neutral inspiration candidates.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--require-previous-knowledge", action="store_true")
    args = parser.parse_args()
    if args.apply and args.project_root is None:
        parser.error("--apply requires --project-root")
    try:
        result = process_packet(_load_packet(args.input), require_previous_knowledge=args.require_previous_knowledge)
        if args.apply:
            write_project_artifacts(args.project_root, result)
        rendered = stable_json(result)
        if args.output:
            atomic_write_text(args.output, rendered)
        else:
            print(rendered, end="")
    except (OSError, InspirationPipelineError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
