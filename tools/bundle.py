from __future__ import annotations

import argparse
from pathlib import Path

from _common import ROOT, atomic_write_text


AUDIENCE_PATHS = {
    "human": ["00_intake/creative-intent.md", "05_production/creative-direction.md", "04_decisions/decision-log.yaml", "04_decisions/uncertainty-register.yaml"],
    "research-agent": ["01_planning/research-plan.yaml", "01_planning/question-register.yaml", "04_decisions/uncertainty-register.yaml", "07_runtime/research-state.json"],
    "production-agent": ["05_production/creative-direction.md", "05_production/production-requirements.yaml", "05_production/acceptance-tests.yaml", "05_production/prototype-backlog.yaml"],
    "audit": ["02_evidence/evidence-ledger.jsonl", "03_knowledge/claims.jsonl", "04_decisions/decision-log.yaml", "06_governance/rights-register.yaml", "06_governance/privacy-review.yaml", "07_runtime/completion-report.json"],
}


def _project_path(root: Path, target: str) -> Path:
    if not target.startswith("project/") or target.count("/") != 1:
        raise ValueError("target must be project/<slug>")
    slug = target.split("/", 1)[1]
    if not slug:
        raise ValueError("target must be project/<slug>")
    projects_root = (root / "projects").resolve()
    project = (projects_root / slug).resolve()
    if project.parent != projects_root:
        raise ValueError("target must identify a direct project directory")
    if not project.is_dir():
        raise FileNotFoundError(f"project not found: {target}")
    return project


def resolve_audience_sources(root: Path, target: str, audience: str) -> list[tuple[str, Path]]:
    """Resolve every declared audience source and reject missing or escaping files."""
    if audience not in AUDIENCE_PATHS:
        raise ValueError(f"unknown audience: {audience}")
    project = _project_path(root, target)
    sources: list[tuple[str, Path]] = []
    for relative in AUDIENCE_PATHS[audience]:
        path = (project / relative).resolve()
        if project not in path.parents:
            raise ValueError(f"audience source escapes project: {relative}")
        if not path.is_file():
            raise FileNotFoundError(f"audience source not found for {audience}: {target}/{relative}")
        sources.append((relative, path))
    return sources


def build_bundle(root: Path, target: str, audience: str) -> str:
    sources = resolve_audience_sources(root, target, audience)
    parts = [f"# {target} — {audience} bundle", "", "Generated from canonical project files. Do not edit this bundle as source.", ""]
    parts.extend(["## Resolved sources", ""])
    parts.extend(f"- `{relative}`" for relative, _ in sources)
    parts.append("")
    for relative, path in sources:
        parts.extend([f"## `{relative}`", "", "```text", path.read_text(encoding="utf-8").rstrip(), "```", ""])
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a minimal audience-specific project context bundle.")
    parser.add_argument("target")
    parser.add_argument("--audience", choices=sorted(AUDIENCE_PATHS), required=True)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        content = build_bundle(args.root.resolve(), args.target, args.audience)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    if args.output:
        atomic_write_text(args.output, content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
