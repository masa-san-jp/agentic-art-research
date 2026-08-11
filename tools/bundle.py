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


def build_bundle(root: Path, target: str, audience: str) -> str:
    if not target.startswith("project/"):
        raise ValueError("target must be project/<slug>")
    slug = target.split("/", 1)[1]
    project = root / "projects" / slug
    if not project.exists():
        raise FileNotFoundError(f"project not found: {target}")
    parts = [f"# {target} — {audience} bundle", "", "Generated from canonical project files. Do not edit this bundle as source.", ""]
    for relative in AUDIENCE_PATHS[audience]:
        path = project / relative
        if not path.exists():
            continue
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

