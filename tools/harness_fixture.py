"""Materialize the deterministic, fully researched project used by harness gates."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from _common import atomic_write_text, load_json, load_yaml, stable_json
from harness import bootstrap


def seed_valid_project(
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
    request_path: Path,
    run_id: str,
    now: str,
) -> Path:
    """Bootstrap then seed a request project with the offline valid fixture."""

    bootstrap(
        protocol_root=protocol_root,
        work_root=work_root,
        output_root=output_root,
        run_id=run_id,
        now=now,
        request_path=request_path,
    )
    project = work_root / "projects" / "harness-study"
    fixture = protocol_root / "tests" / "fixtures" / "harmony"
    for source in sorted(fixture.rglob("*")):
        relative = source.relative_to(fixture)
        if source.is_dir() or relative in {Path("manifest.yaml"), Path("metadata.yaml"), Path("07_runtime/research-state.json")}:
            continue
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    for path in sorted(project.rglob("*")):
        if path.is_file() and path.suffix in {".yaml", ".json", ".jsonl", ".md"}:
            path.write_text(path.read_text(encoding="utf-8").replace("project/harmony-study", "project/harness-study"), encoding="utf-8")

    manifest_path = project / "manifest.yaml"
    manifest = load_yaml(manifest_path) or {}
    manifest["project"]["status"] = "VALIDATING"
    manifest["project"]["updated_at"] = now
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")

    state_path = project / "07_runtime/research-state.json"
    state = load_json(state_path)
    state["status"] = "VALIDATING"
    state["updated_at"] = now
    atomic_write_text(state_path, stable_json(state))

    plan_path = project / "01_planning/research-plan.yaml"
    plan = load_yaml(plan_path) or {}
    plan["minimums"] = {
        "evidence": 0,
        "claims": 0,
        "insights": 0,
        "decisions": 0,
        "requirements": 0,
        "reason": "The deterministic harness fixture uses an explicitly bounded research scope.",
    }
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")

    (project / "04_decisions/rejected-options.yaml").write_text(
        "rejected_options:\n  - id: RO001\n    title: Explanatory text\n    reason: It weakens direct discovery.\n",
        encoding="utf-8",
    )
    (project / "04_decisions/uncertainty-register.yaml").write_text(
        "uncertainties:\n  - id: UN001\n    statement: Venue lighting remains untested.\n",
        encoding="utf-8",
    )
    (project / "03_knowledge/prior-art.jsonl").write_text(
        '{"id":"PA001","work_title":"Related work","source_url":"https://example.com/related-work","difference":"The fixture uses spatial discovery."}\n',
        encoding="utf-8",
    )
    (project / "04_decisions/self-repetition-review.yaml").write_text(
        "reviews:\n  - id: SR001\n    scope: Concept\n    prior_work_refs: [PA001]\n    risk_level: LOW\n    assessment: The fixture preserves a distinct interaction.\n    mitigation: Preserve the documented interruption.\n    reviewed_at: '2026-08-25T00:00:00+09:00'\n",
        encoding="utf-8",
    )
    (project / "06_governance/rights-register.yaml").write_text("rights:\n  - id: RT001\n    status: CLEARED\n", encoding="utf-8")
    (project / "06_governance/privacy-review.yaml").write_text(
        "status: COMPLETE\nreviewed_at: '2026-08-25T00:00:00+09:00'\nprivate_raw_in_git: false\nfindings: []\n",
        encoding="utf-8",
    )

    hypothesis = load_json(protocol_root / "tests/fixtures/schema-valid/production-hypothesis.json")
    hypothesis["single_hypothesis_rationale"] = None
    rejected = dict(hypothesis)
    rejected.update({"id": "PH002", "title": "Fixed cue alternative", "status": "REJECTED", "recommendation": "REJECTED", "uncertainties": []})
    (project / "04_decisions/production-hypotheses.yaml").write_text(
        yaml.safe_dump({"hypotheses": [hypothesis, rejected]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    comparison = load_json(protocol_root / "tests/fixtures/schema-valid/hypothesis-comparison.json")
    (project / "04_decisions/hypothesis-comparison.yaml").write_text(yaml.safe_dump({"comparisons": [comparison]}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    prototype = load_json(protocol_root / "tests/fixtures/schema-valid/prototype-plan.json")
    (project / "05_production/prototype-plans.yaml").write_text(yaml.safe_dump({"prototype_plans": [prototype]}, sort_keys=False, allow_unicode=True), encoding="utf-8")

    run_log = project / "07_runtime/run-log.jsonl"
    log_lines = run_log.read_text(encoding="utf-8").splitlines()
    if len(log_lines) >= 2:
        log_lines.pop(-2)
        last = json.loads(log_lines[-1])
        last.update({"from_status": "READY_FOR_PRODUCTION", "to_status": "VALIDATING"})
        log_lines[-1] = json.dumps(last, sort_keys=True)
    run_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    with run_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event_id": "E2E-SEARCH", "event_type": "SEARCH_ATTEMPT", "question_id": "Q001", "strategy_id": "fixture"}) + "\n")
    return project


__all__ = ["seed_valid_project"]
