from __future__ import annotations

import argparse
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from _common import (
    InputParseError,
    PROJECT_REQUIRED_FILES,
    ROOT,
    iter_project_dirs,
    load_json,
    load_yaml,
    read_jsonl_with_lines,
)


SCHEMA_FOR_JSONL = {
    "02_evidence/evidence-ledger.jsonl": "evidence",
    "03_knowledge/claims.jsonl": "claim",
}
SCHEMA_FOR_YAML_COLLECTION = {
    "04_decisions/insight-register.yaml": ("insights", "insight"),
    "04_decisions/decision-log.yaml": ("decisions", "decision"),
    "05_production/production-requirements.yaml": ("requirements", "requirement"),
}
SCHEMA_FOR_JSON = {
    "07_runtime/completion-report.json": "completion-report",
}
DOMAIN_SCHEMAS = (
    "project-manifest",
    "evidence",
    "claim",
    "insight",
    "decision",
    "requirement",
    "completion-report",
)


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    message: str
    line: int | None = None
    field: str | None = None
    remediation: str = "Inspect the reported value and correct the source file."

    def render(self) -> str:
        location = self.path
        if self.line is not None:
            location += f":{self.line}"
        if self.field:
            location += f"#{self.field}"
        return f"{location}: [{self.rule}] {self.message}; remediation: {self.remediation}"


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _exception_finding(root: Path, path: Path, rule: str, exc: Exception) -> Finding:
    if isinstance(exc, InputParseError):
        return Finding(
            _relative_path(root, exc.path),
            rule,
            exc.message,
            line=exc.line,
            field=exc.field,
            remediation="Fix the syntax or duplicate key at the reported location, then rerun validation.",
        )
    return Finding(
        _relative_path(root, path),
        rule,
        str(exc),
        remediation="Inspect the file and correct the reported input error, then rerun validation.",
    )


def _field_path(parts: list[Any] | tuple[Any, ...]) -> str:
    field = ""
    for part in parts:
        if isinstance(part, int):
            field += f"[{part}]"
        else:
            field += f".{part}" if field else str(part)
    return field or "$"


def _schema_error_field(error: Any) -> str:
    field = _field_path(tuple(error.absolute_path))
    if error.validator == "required":
        match = re.match(r"'([^']+)' is a required property$", error.message)
        if match:
            field = f"{field}.{match.group(1)}" if field != "$" else match.group(1)
    return field


def _schema_remediation(error: Any) -> str:
    if error.validator == "required":
        return "Add the required property named in the message."
    if error.validator == "additionalProperties":
        return "Remove the unknown property or update the canonical schema deliberately."
    if error.validator == "enum":
        return "Use one of the values declared by the canonical vocabulary."
    if error.validator == "pattern":
        return "Use a value matching the canonical ID, URI, timestamp, or hash pattern."
    if error.validator == "minItems":
        return "Add the minimum required number of items."
    if error.validator == "minLength":
        return "Provide a non-empty value."
    if error.validator == "anyOf":
        return "Provide at least one of the allowed evidence or traceability bases."
    if error.validator == "type":
        return "Change the value to the type required by the schema."
    return "Correct the value to satisfy the canonical JSON Schema."


def _schema_findings(
    root: Path,
    path: Path,
    validator: Draft202012Validator,
    instance: Any,
    *,
    line: int | None = None,
    field_prefix: str | None = None,
) -> list[Finding]:
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.validator or ""),
    )
    findings: list[Finding] = []
    for error in errors:
        field = _schema_error_field(error)
        if field_prefix:
            field = field_prefix if field == "$" else f"{field_prefix}{field[1:]}"
        findings.append(
            Finding(
                _relative_path(root, path),
                f"SCHEMA:{error.validator or 'validation'}",
                error.message,
                line=line,
                field=field,
                remediation=_schema_remediation(error),
            )
        )
    return findings


def _load_schema_validators(root: Path, findings: list[Finding]) -> dict[str, Draft202012Validator]:
    documents: dict[str, dict[str, Any]] = {}
    schemas_root = root / "schemas"
    for path in sorted(schemas_root.glob("*.json")):
        try:
            document = load_json(path)
        except Exception as exc:
            findings.append(_exception_finding(root, path, "SCHEMA-JSON", exc))
            continue
        if not isinstance(document, dict) or "$schema" not in document:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "SCHEMA-META",
                    "missing $schema",
                    field="$schema",
                    remediation="Declare the Draft 2020-12 meta-schema in the schema document.",
                )
            )
            continue
        try:
            Draft202012Validator.check_schema(document)
        except SchemaError as exc:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "SCHEMA-DEFINITION",
                    str(exc.message),
                    field="$",
                    remediation="Fix the schema definition before validating project data.",
                )
            )
            continue
        documents[path.name] = document

    registry = Registry().with_resources(
        [
            (document["$id"], Resource.from_contents(document))
            for document in documents.values()
            if isinstance(document.get("$id"), str)
        ]
    )
    validators: dict[str, Draft202012Validator] = {}
    for schema_name in DOMAIN_SCHEMAS:
        schema_path = schemas_root / f"{schema_name}.schema.json"
        document = documents.get(schema_path.name)
        if document is None:
            findings.append(
                Finding(
                    _relative_path(root, schema_path),
                    "SCHEMA-MISSING",
                    "canonical domain schema is unavailable",
                    remediation="Restore the required schema file and rerun validation.",
                )
            )
            continue
        try:
            validators[schema_name] = Draft202012Validator(document, registry=registry)
        except Exception as exc:
            findings.append(
                Finding(
                    _relative_path(root, schema_path),
                    "SCHEMA-REFERENCE",
                    str(exc),
                    remediation="Fix the schema reference or restore the referenced common definition.",
                )
            )
    return validators


def _validate_yaml_collection(
    root: Path,
    path: Path,
    value: Any,
    key: str,
    validator: Draft202012Validator | None,
    findings: list[Finding],
) -> None:
    relative = _relative_path(root, path)
    if not isinstance(value, dict):
        findings.append(
            Finding(
                relative,
                "STRUCTURE:type",
                "canonical YAML document must be an object",
                field="$",
                remediation=f"Wrap the records in an object containing the {key!r} list.",
            )
        )
        return
    records = value.get(key)
    if not isinstance(records, list):
        findings.append(
            Finding(
                relative,
                "STRUCTURE:type",
                f"{key} must be a list",
                field=key,
                remediation=f"Set {key} to a YAML list of canonical objects.",
            )
        )
        return
    if validator is None:
        return
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            findings.append(
                Finding(
                    relative,
                    "STRUCTURE:type",
                    f"{key}[{index}] must be an object",
                    field=f"{key}[{index}]",
                    remediation="Replace the item with a mapping/object matching the canonical schema.",
                )
            )
            continue
        findings.extend(_schema_findings(root, path, validator, record, field_prefix=f"{key}[{index}]"))


def _secret_findings(root: Path, path: Path, patterns: list[dict[str, Any]]) -> list[Finding]:
    try:
        content = path.read_bytes()
        if b"\x00" in content:
            return []
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[Finding] = []
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns:
        if not isinstance(pattern, dict) or not isinstance(pattern.get("id"), str) or not isinstance(pattern.get("pattern"), str):
            continue
        try:
            compiled.append((pattern["id"], re.compile(pattern["pattern"])))
        except re.error:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "SECURITY-PATTERN",
                    f"invalid secret pattern {pattern['id']!r}",
                    remediation="Fix the regular expression in config/access-policy.yaml.",
                )
            )
    for line_number, line in enumerate(text.splitlines(), 1):
        for pattern_id, regex in compiled:
            if regex.search(line):
                findings.append(
                    Finding(
                        _relative_path(root, path),
                        "SECRET-SCAN",
                        f"likely secret matched pattern {pattern_id}",
                        line=line_number,
                        field="$",
                        remediation="Remove the secret from the repository and rotate it in its source system.",
                    )
                )
    return findings


def validate_repository(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    vocab_path = root / "config" / "vocabularies.yaml"
    access_path = root / "config" / "access-policy.yaml"
    try:
        vocab = load_yaml(vocab_path) or {}
    except Exception as exc:
        findings.append(_exception_finding(root, vocab_path, "YAML", exc))
        vocab = {}
    try:
        access = load_yaml(access_path) or {}
    except Exception as exc:
        findings.append(_exception_finding(root, access_path, "YAML", exc))
        access = {}

    for path in sorted((root / "config").glob("*.yaml")):
        if path in {vocab_path, access_path}:
            continue
        try:
            load_yaml(path)
        except Exception as exc:
            findings.append(_exception_finding(root, path, "YAML", exc))

    schema_validators = _load_schema_validators(root, findings)

    forbidden = set(access.get("forbidden_extensions", [])) if isinstance(access, dict) else set()
    forbidden_filenames = access.get("forbidden_filenames", []) if isinstance(access, dict) else []
    secret_patterns = access.get("secret_patterns", []) if isinstance(access, dict) else []
    ignored_parts = {".git", ".venv", "__pycache__"}
    for path in root.rglob("*"):
        if not path.is_file() or ignored_parts.intersection(path.parts):
            continue
        if path.suffix.lower() in forbidden:
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "DATA-BOUNDARY",
                    f"forbidden extension {path.suffix}",
                    remediation="Remove the raw/private artifact from the repository and retain only permitted metadata.",
                )
            )
        if any(fnmatch.fnmatch(path.name, pattern) for pattern in forbidden_filenames if isinstance(pattern, str)):
            findings.append(
                Finding(
                    _relative_path(root, path),
                    "DATA-BOUNDARY",
                    f"forbidden filename {path.name}",
                    remediation="Remove the credential or private artifact and retain only permitted opaque metadata.",
                )
            )
        findings.extend(_secret_findings(root, path, secret_patterns))

    statuses = set(vocab.get("project_statuses", [])) if isinstance(vocab, dict) else set()
    for project in iter_project_dirs(root):
        relative_project = project.relative_to(root)
        for required in PROJECT_REQUIRED_FILES:
            if not (project / required).exists():
                findings.append(
                    Finding(
                        str(relative_project / required),
                        "PROJECT-STRUCTURE",
                        "required file missing",
                        remediation="Create the file from templates/project or restore the canonical project structure.",
                    )
                )

        manifest_path = project / "manifest.yaml"
        manifest: Any = None
        try:
            manifest = load_yaml(manifest_path) or {}
        except Exception as exc:
            findings.append(_exception_finding(root, manifest_path, "YAML", exc))
        if isinstance(manifest, dict):
            project_data = manifest.get("project", {})
            expected_id = f"project/{project.name}"
            if project_data.get("id") != expected_id:
                findings.append(
                    Finding(
                        _relative_path(root, manifest_path),
                        "PROJECT-ID",
                        f"expected {expected_id}",
                        field="project.id",
                        remediation="Set project.id to project/<directory-slug>.",
                    )
                )
            status = project_data.get("status")
            if status not in statuses:
                findings.append(
                    Finding(
                        _relative_path(root, manifest_path),
                        "PROJECT-STATUS",
                        f"invalid status {status!r}",
                        field="project.status",
                        remediation="Use a project status from config/vocabularies.yaml.",
                    )
                )
            classification = (manifest.get("access") or {}).get("classification")
            if classification in {"PRIVATE_RAW", "RESTRICTED"}:
                findings.append(
                    Finding(
                        _relative_path(root, manifest_path),
                        "DATA-BOUNDARY",
                        "project manifest cannot use a Git-prohibited classification",
                        field="access.classification",
                        remediation="Use PROJECT_INTERNAL, PUBLIC_CITABLE, or an approved PRIVATE_DERIVED boundary.",
                    )
                )
            manifest_validator = schema_validators.get("project-manifest")
            if manifest_validator:
                findings.extend(_schema_findings(root, manifest_path, manifest_validator, manifest))

        for path in sorted(project.rglob("*.yaml")):
            if path == manifest_path:
                continue
            try:
                value = load_yaml(path)
            except Exception as exc:
                findings.append(_exception_finding(root, path, "YAML", exc))
                continue
            relative = path.relative_to(project).as_posix()
            target = SCHEMA_FOR_YAML_COLLECTION.get(relative)
            if target:
                key, schema_name = target
                _validate_yaml_collection(root, path, value, key, schema_validators.get(schema_name), findings)

        state_value: Any = None
        for path in sorted(project.rglob("*.json")):
            try:
                value = load_json(path)
            except Exception as exc:
                findings.append(_exception_finding(root, path, "JSON", exc))
                continue
            relative = path.relative_to(project).as_posix()
            schema_name = SCHEMA_FOR_JSON.get(relative)
            if schema_name:
                validator = schema_validators.get(schema_name)
                if validator:
                    findings.extend(_schema_findings(root, path, validator, value))
            if relative == "07_runtime/research-state.json":
                state_value = value

        for path in sorted(project.rglob("*.jsonl")):
            try:
                records = read_jsonl_with_lines(path)
            except Exception as exc:
                findings.append(_exception_finding(root, path, "JSONL", exc))
                continue
            relative = path.relative_to(project).as_posix()
            schema_name = SCHEMA_FOR_JSONL.get(relative)
            validator = schema_validators.get(schema_name) if schema_name else None
            for line_number, record in records:
                if validator:
                    findings.extend(_schema_findings(root, path, validator, record, line=line_number))
                if "approved-snapshots" in path.parts and record.get("sensitivity") in {"PRIVATE_RAW", "RESTRICTED"}:
                    findings.append(
                        Finding(
                            _relative_path(root, path),
                            "DATA-BOUNDARY",
                            "prohibited record in approved snapshots",
                            line=line_number,
                            field="sensitivity",
                            remediation="Remove the prohibited record from approved-snapshots and retain only allowed metadata.",
                        )
                    )

        if isinstance(state_value, dict) and isinstance(manifest, dict):
            manifest_status = (manifest.get("project") or {}).get("status")
            if state_value.get("status") != manifest_status:
                state_path = project / "07_runtime" / "research-state.json"
                findings.append(
                    Finding(
                        _relative_path(root, state_path),
                        "STATE-SYNC",
                        "runtime status differs from manifest",
                        field="status",
                        remediation="Set research-state.status and manifest.project.status to the same lifecycle state.",
                    )
                )

    return sorted(findings, key=lambda item: (item.path, item.line or 0, item.field or "", item.rule, item.message))


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
