from __future__ import annotations

import argparse
from pathlib import Path

from _common import ROOT, load_yaml


class DocumentationCheckError(ValueError):
    pass


def check_documentation(root: Path = ROOT) -> list[str]:
    config_path = root / "config" / "documentation.yaml"
    if not config_path.exists():
        return [f"{config_path}: missing documentation contract"]

    try:
        config = load_yaml(config_path) or {}
    except Exception as exc:
        return [f"{config_path}: cannot parse documentation contract: {exc}"]

    documents = config.get("documents")
    if not isinstance(documents, list):
        return [f"{config_path}: documents must be a list"]

    findings: list[str] = []
    root_resolved = root.resolve()
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            findings.append(f"{config_path}: documents[{index}] must be a mapping")
            continue
        relative_path = document.get("path")
        required_sections = document.get("required_sections")
        if not isinstance(relative_path, str) or not relative_path:
            findings.append(f"{config_path}: documents[{index}].path must be a non-empty string")
            continue
        if not isinstance(required_sections, list) or not all(
            isinstance(section, str) and section for section in required_sections
        ):
            findings.append(f"{config_path}: {relative_path}: required_sections must be non-empty strings")
            continue

        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError:
            findings.append(f"{config_path}: {relative_path}: path escapes repository root")
            continue
        if not candidate.is_file():
            findings.append(f"{candidate}: missing required document")
            continue
        text = candidate.read_text(encoding="utf-8")
        for section in required_sections:
            if section not in text:
                findings.append(f"{candidate}: missing required section {section!r}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Check required operational documentation")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="fail when the documentation contract is not met")
    args = parser.parse_args()

    findings = check_documentation(args.root.resolve())
    if findings:
        for finding in findings:
            print(f"ERROR: {finding}")
        return 1
    print("OK: documentation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
