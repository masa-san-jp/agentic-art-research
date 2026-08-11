from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _common import PROJECT_REQUIRED_FILES, ROOT, iter_project_dirs, load_json, load_yaml, read_jsonl


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.path}: [{self.rule}] {self.message}"


def _walk_scalars(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_scalars(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_scalars(child)
    else:
        yield value


def validate_repository(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    vocab_path = root / "config" / "vocabularies.yaml"
    access_path = root / "config" / "access-policy.yaml"
    try:
        vocab = load_yaml(vocab_path)
    except Exception as exc:
        findings.append(Finding(str(vocab_path), "YAML", str(exc)))
        vocab = {}
    try:
        access = load_yaml(access_path)
    except Exception as exc:
        findings.append(Finding(str(access_path), "YAML", str(exc)))
        access = {}

    for path in sorted((root / "config").glob("*.yaml")):
        try:
            load_yaml(path)
        except Exception as exc:
            findings.append(Finding(str(path.relative_to(root)), "YAML", str(exc)))

    for path in sorted((root / "schemas").glob("*.json")):
        try:
            schema = load_json(path)
            if not isinstance(schema, dict) or "$schema" not in schema:
                findings.append(Finding(str(path.relative_to(root)), "SCHEMA-META", "missing $schema"))
        except Exception as exc:
            findings.append(Finding(str(path.relative_to(root)), "JSON", str(exc)))

    forbidden = set(access.get("forbidden_extensions", [])) if isinstance(access, dict) else set()
    ignored_parts = {".git", ".venv", "__pycache__"}
    for path in root.rglob("*"):
        if not path.is_file() or ignored_parts.intersection(path.parts):
            continue
        if path.suffix.lower() in forbidden:
            findings.append(Finding(str(path.relative_to(root)), "DATA-BOUNDARY", f"forbidden extension {path.suffix}"))

    statuses = set(vocab.get("project_statuses", [])) if isinstance(vocab, dict) else set()
    for project in iter_project_dirs(root):
        relative_project = project.relative_to(root)
        for required in PROJECT_REQUIRED_FILES:
            if not (project / required).exists():
                findings.append(Finding(str(relative_project / required), "PROJECT-STRUCTURE", "required file missing"))
        manifest_path = project / "manifest.yaml"
        try:
            manifest = load_yaml(manifest_path) or {}
            project_data = manifest.get("project", {})
            expected_id = f"project/{project.name}"
            if project_data.get("id") != expected_id:
                findings.append(Finding(str(manifest_path.relative_to(root)), "PROJECT-ID", f"expected {expected_id}"))
            status = project_data.get("status")
            if status not in statuses:
                findings.append(Finding(str(manifest_path.relative_to(root)), "PROJECT-STATUS", f"invalid status {status!r}"))
            classification = (manifest.get("access") or {}).get("classification")
            if classification in {"PRIVATE_RAW", "RESTRICTED"}:
                findings.append(Finding(str(manifest_path.relative_to(root)), "DATA-BOUNDARY", "project manifest cannot use a Git-prohibited classification"))
        except Exception as exc:
            findings.append(Finding(str(manifest_path.relative_to(root)), "YAML", str(exc)))

        for path in sorted(project.rglob("*.yaml")):
            try:
                load_yaml(path)
            except Exception as exc:
                findings.append(Finding(str(path.relative_to(root)), "YAML", str(exc)))
        for path in sorted(project.rglob("*.json")):
            try:
                load_json(path)
            except Exception as exc:
                findings.append(Finding(str(path.relative_to(root)), "JSON", str(exc)))
        for path in sorted(project.rglob("*.jsonl")):
            try:
                records = read_jsonl(path)
                if "approved-snapshots" in path.parts:
                    for record in records:
                        if record.get("sensitivity") in {"PRIVATE_RAW", "RESTRICTED"}:
                            findings.append(Finding(str(path.relative_to(root)), "DATA-BOUNDARY", "prohibited record in approved snapshots"))
            except Exception as exc:
                findings.append(Finding(str(path.relative_to(root)), "JSONL", str(exc)))

        state_path = project / "07_runtime" / "research-state.json"
        if state_path.exists():
            try:
                state = load_json(state_path)
                manifest = load_yaml(manifest_path) or {}
                manifest_status = (manifest.get("project") or {}).get("status")
                if state.get("status") != manifest_status:
                    findings.append(Finding(str(state_path.relative_to(root)), "STATE-SYNC", "runtime status differs from manifest"))
            except Exception:
                pass

    return sorted(findings, key=lambda item: (item.path, item.rule, item.message))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate repository contracts and safety boundaries.")
    parser.add_argument("--check", action="store_true", help="Validate without generating or modifying files.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    findings = validate_repository(args.root.resolve())
    if findings:
        for finding in findings:
            print(finding.render())
        print(f"FAILED: {len(findings)} finding(s)")
        return 1
    print("OK: repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

