"""Shared project and snapshot readers for production handoff tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from _common import load_json, load_yaml, read_jsonl


PROJECT_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class HandoffInputError(ValueError):
    """Raised when canonical handoff inputs cannot be resolved safely."""


@dataclass(frozen=True)
class HandoffSources:
    root: Path
    project: Path
    project_id: str
    manifest: dict[str, Any]
    project_data: dict[str, Any]
    constraints: dict[str, Any]
    completion_report: dict[str, Any]
    hypotheses_document: dict[str, Any]
    comparison_document: dict[str, Any]
    prototype_document: dict[str, Any]
    requirements_document: dict[str, Any]
    visual_language_document: dict[str, Any]
    acceptance_tests_document: dict[str, Any]
    decisions_document: dict[str, Any]
    insights_document: dict[str, Any]
    hypotheses: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    prototype_plans: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    acceptance_tests: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    insights: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    handoff: dict[str, Any]


def resolve_project(root: Path, target: str) -> Path:
    """Resolve project/<slug>, <slug>, or projects/<slug> within root."""

    if target.startswith("project/"):
        slug = target.split("/", 1)[1]
        if not PROJECT_SLUG.fullmatch(slug):
            raise HandoffInputError("target must be project/<lower-case-kebab-case-slug>")
        candidate = root / "projects" / slug
    elif target.startswith("projects/"):
        slug = target.split("/", 1)[1]
        if not PROJECT_SLUG.fullmatch(slug):
            raise HandoffInputError("target must be projects/<lower-case-kebab-case-slug>")
        candidate = root / "projects" / slug
    else:
        slug = Path(target).name
        if Path(target).is_absolute():
            candidate = Path(target)
        elif PROJECT_SLUG.fullmatch(target):
            candidate = root / "projects" / target
        else:
            raise HandoffInputError("target must be project/<lower-case-kebab-case-slug>")

    project = candidate.resolve()
    projects_root = (root / "projects").resolve()
    if project.parent != projects_root:
        raise HandoffInputError("target must identify a direct project directory")
    if not project.is_dir():
        raise HandoffInputError(f"project not found: {project}")
    return project


def _mapping(path: Path, value: Any, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HandoffInputError(f"{path}: {label} must be a mapping")
    return value


def _collection(path: Path, value: Any, key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = _mapping(path, value, label="document")
    records = document.get(key, [])
    if not isinstance(records, list):
        raise HandoffInputError(f"{path}: {key} must be a list")
    if any(not isinstance(record, dict) for record in records):
        raise HandoffInputError(f"{path}: {key} must contain only mappings")
    return document, list(records)


def sorted_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return records in the contract's stable ID order without mutating input."""

    return sorted(records, key=lambda record: str(record.get("id", "")))


def yaml_text(value: Any) -> str:
    return yaml.safe_dump(
        value,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )


def collection_yaml(document: dict[str, Any], key: str) -> str:
    normalized = dict(document)
    normalized[key] = sorted_records(list(document.get(key, [])))
    return yaml_text(normalized)


def load_handoff_sources(root: Path, target: str) -> HandoffSources:
    root = root.resolve()
    project = resolve_project(root, target)
    manifest_path = project / "manifest.yaml"
    manifest = _mapping(manifest_path, load_yaml(manifest_path), label="manifest")
    if manifest.get("workflow_mode", "RESEARCH_ONLY") != "PRODUCTION_HANDOFF":
        raise HandoffInputError(
            f"{manifest_path}: workflow_mode must be PRODUCTION_HANDOFF before building or exporting a handoff"
        )
    project_data = _mapping(manifest_path, manifest.get("project"), label="manifest.project")
    project_id = project_data.get("id")
    if not isinstance(project_id, str) or not project_id:
        raise HandoffInputError(f"{manifest_path}: manifest.project.id must be a non-empty string")

    hypotheses_path = project / "04_decisions" / "production-hypotheses.yaml"
    hypotheses_document, hypotheses = _collection(
        hypotheses_path,
        load_yaml(hypotheses_path),
        "hypotheses",
    )
    comparison_path = project / "04_decisions" / "hypothesis-comparison.yaml"
    comparison_document, comparisons = _collection(
        comparison_path,
        load_yaml(comparison_path),
        "comparisons",
    )
    prototype_path = project / "05_production" / "prototype-plans.yaml"
    prototype_document, prototype_plans = _collection(
        prototype_path,
        load_yaml(prototype_path),
        "prototype_plans",
    )
    requirements_path = project / "05_production" / "production-requirements.yaml"
    requirements_document, requirements = _collection(
        requirements_path,
        load_yaml(requirements_path),
        "requirements",
    )
    visual_language_path = project / "05_production" / "visual-language.yaml"
    visual_language_document = _mapping(
        visual_language_path,
        load_yaml(visual_language_path),
        label="visual language",
    )
    acceptance_path = project / "05_production" / "acceptance-tests.yaml"
    acceptance_tests_document, acceptance_tests = _collection(
        acceptance_path,
        load_yaml(acceptance_path),
        "acceptance_tests",
    )
    decisions_path = project / "04_decisions" / "decision-log.yaml"
    decisions_document, decisions = _collection(decisions_path, load_yaml(decisions_path), "decisions")
    insights_path = project / "04_decisions" / "insight-register.yaml"
    insights_document, insights = _collection(insights_path, load_yaml(insights_path), "insights")

    evidence_path = project / "02_evidence" / "evidence-ledger.jsonl"
    claims_path = project / "03_knowledge" / "claims.jsonl"
    evidence = read_jsonl(evidence_path)
    claims = read_jsonl(claims_path)

    constraints_path = project / "00_intake" / "constraints.yaml"
    constraints = _mapping(constraints_path, load_yaml(constraints_path), label="constraints")
    completion_path = project / "07_runtime" / "completion-report.json"
    completion_report = _mapping(completion_path, load_json(completion_path), label="completion report")

    handoff_path = project / "05_production" / "production-handoff.yaml"
    handoff_value = load_yaml(handoff_path) if handoff_path.exists() else {}
    handoff = _mapping(handoff_path, handoff_value, label="handoff")

    return HandoffSources(
        root=root,
        project=project,
        project_id=project_id,
        manifest=manifest,
        project_data=project_data,
        constraints=constraints,
        completion_report=completion_report,
        hypotheses_document=hypotheses_document,
        comparison_document=comparison_document,
        prototype_document=prototype_document,
        requirements_document=requirements_document,
        visual_language_document=visual_language_document,
        acceptance_tests_document=acceptance_tests_document,
        decisions_document=decisions_document,
        insights_document=insights_document,
        hypotheses=hypotheses,
        comparisons=comparisons,
        prototype_plans=prototype_plans,
        requirements=requirements,
        acceptance_tests=acceptance_tests,
        decisions=decisions,
        insights=insights,
        evidence=evidence,
        claims=claims,
        handoff=handoff,
    )
