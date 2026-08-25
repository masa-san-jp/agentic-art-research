"""Export imported production test results as deterministic research signals."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from _common import ROOT, load_json, load_yaml, read_jsonl, stable_json, yaml_list
from import_production_result import (
    ResultImportError,
    _load_policy,
    _schema_validator,
    _security_scan,
    _validate_schema,
    result_sha256,
)
from validate import validate_repository


SCHEMA_ID = "research-signal-export/v1"
RESULT_ID_PATTERN = re.compile(r"^PR[0-9]{3,}$")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<![A-Za-z0-9])\+?[0-9][0-9 ()-]{7,}[0-9](?![A-Za-z0-9])")
ACCOUNT_ID_PATTERN = re.compile(r"\b(?:account|respondent|user|customer)[ _-]?id\s*[:=]", re.IGNORECASE)
PRIVATE_HOST_PATTERN = re.compile(
    r"https?://(?:localhost|127(?:\.[0-9]{1,3}){3}|10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|172\.(?:1[6-9]|2[0-9]|3[0-1])(?:\.[0-9]{1,3}){2})(?:[/?:#]|$)",
    re.IGNORECASE,
)


class FeedbackSignalExportError(ValueError):
    """Raised when a research-signal export cannot be safely completed."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "research-signal-export",
        field: str | None = None,
        rule: str = "FEEDBACK-EXPORT",
        remediation: str = "Correct the imported result or project source, then retry.",
    ) -> None:
        self.path = path
        self.field = field
        self.rule = rule
        self.remediation = remediation
        super().__init__(message)

    def __str__(self) -> str:
        location = self.path
        if self.field:
            location += f"#{self.field}"
        return f"{location}: [{self.rule}] {self.args[0]}; remediation: {self.remediation}"


def _project_path(root: Path, target: str) -> Path:
    if not target.startswith("project/") or target.count("/") != 1:
        raise FeedbackSignalExportError(
            "target must be project/<slug>",
            field="target",
            rule="FEEDBACK-EXPORT-REFERENCE",
            remediation="Use a project/<slug> target from the working root.",
        )
    projects_root = (root / "projects").resolve()
    project = (projects_root / target.split("/", 1)[1]).resolve()
    if project.parent != projects_root or not project.is_dir():
        raise FeedbackSignalExportError(
            f"project not found: {target}",
            path=target,
            rule="FEEDBACK-EXPORT-REFERENCE",
            remediation="Use an existing imported research project.",
        )
    return project


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _schema(root: Path) -> tuple[dict[str, Any], Draft202012Validator, Draft202012Validator]:
    path = root / "schemas" / "research-signal-export.schema.json"
    try:
        schema = load_json(path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        signal_schema = copy.deepcopy(schema["$defs"]["signal"])
        signal_schema["$defs"] = schema["$defs"]
        signal_validator = Draft202012Validator(signal_schema, format_checker=FormatChecker())
    except Exception as exc:
        raise FeedbackSignalExportError(
            f"export schema is unavailable or invalid: {exc}",
            path=str(path),
            rule="FEEDBACK-EXPORT-CONTRACT",
            remediation="Restore a valid research-signal-export schema.",
        ) from exc
    return schema, validator, signal_validator


def _validate_instance(value: dict[str, Any], validator: Draft202012Validator, *, path: str, rule: str) -> None:
    errors = sorted(validator.iter_errors(value), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if errors:
        error = errors[0]
        field = ".".join(str(part) for part in error.absolute_path) or "$"
        raise FeedbackSignalExportError(
            error.message,
            path=path,
            field=field,
            rule=rule,
            remediation="Correct the source record so it conforms to the export contract.",
        )


def _load_imported_result(project: Path, result_id: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    feedback_path = project / "07_runtime" / "production-feedback-imports.jsonl"
    records = [record for record in read_jsonl(feedback_path) if record.get("result_id") == result_id]
    if not records:
        raise FeedbackSignalExportError(
            f"result {result_id!r} is not present in the import log",
            path=str(feedback_path),
            field="result_id",
            rule="FEEDBACK-EXPORT-NOT-IMPORTED",
            remediation="Import and validate the production result before exporting signals.",
        )
    if len(records) != 1:
        raise FeedbackSignalExportError(
            f"result {result_id!r} appears {len(records)} times in the import log",
            path=str(feedback_path),
            field="result_id",
            rule="FEEDBACK-EXPORT-IDEMPOTENCY",
            remediation="Retain exactly one imported result record for each result ID.",
        )
    result = records[0]
    run_log_path = project / "07_runtime" / "run-log.jsonl"
    audit_events = [
        event
        for event in read_jsonl(run_log_path)
        if event.get("event_type") == "PRODUCTION_FEEDBACK_IMPORTED" and event.get("result_id") == result_id
    ]
    if len(audit_events) != 1:
        raise FeedbackSignalExportError(
            f"result {result_id!r} has no unique import audit event",
            path=str(run_log_path),
            field="result_id",
            rule="FEEDBACK-EXPORT-NOT-IMPORTED",
            remediation="Complete the guarded production-result import before exporting signals.",
        )
    audit = audit_events[0]
    calculated = result_sha256(result)
    integrity = result.get("integrity") if isinstance(result.get("integrity"), dict) else {}
    if integrity.get("content_sha256") != calculated or audit.get("result_sha256") != calculated:
        raise FeedbackSignalExportError(
            "imported result hash does not match its integrity and audit records",
            path=str(feedback_path),
            field="integrity.content_sha256",
            rule="FEEDBACK-EXPORT-HASH",
            remediation="Restore the immutable imported result and matching import audit event.",
        )
    return result, audit, feedback_path


def _privacy_scan(root: Path, result: dict[str, Any], path: Path, policy: dict[str, Any]) -> None:
    try:
        _security_scan(root, result, path, policy)
    except ResultImportError as exc:
        raise FeedbackSignalExportError(
            str(exc),
            path=exc.path,
            field=exc.field,
            rule="FEEDBACK-EXPORT-PRIVACY",
            remediation="Remove private, identifying, secret, path, or credential-bearing content and re-import the result.",
        ) from exc

    free_text_fields = (
        "conditions",
        "statement",
        "limitations",
        "method",
        "reason",
        "impact",
        "resolution_condition",
    )

    def visit(value: Any, field: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{field}.{key}")
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{field}[{index}]")
            return
        if not isinstance(value, str):
            return
        if EMAIL_PATTERN.search(value) or ACCOUNT_ID_PATTERN.search(value):
            raise FeedbackSignalExportError(
                "free text contains an identifying field or email address",
                path=str(path),
                field=field,
                rule="FEEDBACK-EXPORT-PRIVACY",
                remediation="Replace identifying content with an approved derived statement before import.",
            )
        is_free_text = any(field.endswith(f".{name}") for name in free_text_fields)
        if is_free_text and PHONE_PATTERN.search(value):
            raise FeedbackSignalExportError(
                "free text contains a phone-like identifier",
                path=str(path),
                field=field,
                rule="FEEDBACK-EXPORT-PRIVACY",
                remediation="Remove phone numbers and retain only an anonymized research observation.",
            )
        if is_free_text and re.search(r"https?://", value, re.IGNORECASE):
            raise FeedbackSignalExportError(
                "free text contains an asset or external URL",
                path=str(path),
                field=field,
                rule="FEEDBACK-EXPORT-PRIVACY",
                remediation="Remove URLs from free text and retain only an approved derived observation.",
            )
        if PRIVATE_HOST_PATTERN.search(value):
            raise FeedbackSignalExportError(
                "free text contains a private asset URL",
                path=str(path),
                field=field,
                rule="FEEDBACK-EXPORT-PRIVACY",
                remediation="Use a non-sensitive opaque reference or remove the private URL.",
            )

    visit(result)


def _source_records(project: Path, result: dict[str, Any], result_id: str) -> list[dict[str, Any]]:
    acceptance_tests = yaml_list(project / "05_production" / "acceptance-tests.yaml", "acceptance_tests")
    requirements = yaml_list(project / "05_production" / "production-requirements.yaml", "requirements")
    acceptance_by_id = {record.get("id"): record for record in acceptance_tests if isinstance(record.get("id"), str)}
    requirement_by_id = {record.get("id"): record for record in requirements if isinstance(record.get("id"), str)}
    test_results = result.get("test_results")
    if not isinstance(test_results, list):
        raise FeedbackSignalExportError(
            "production result test_results must be a list",
            field="test_results",
            rule="FEEDBACK-EXPORT-CONTRACT",
        )

    observations = result.get("observations", [])
    if not isinstance(observations, list):
        raise FeedbackSignalExportError("production result observations must be a list", field="observations", rule="FEEDBACK-EXPORT-CONTRACT")
    seen_test_ids: set[str] = set()
    result_hash = result_sha256(result)
    signals: list[dict[str, Any]] = []
    for index, test_result in enumerate(test_results):
        if not isinstance(test_result, dict):
            raise FeedbackSignalExportError(
                "test result must be an object",
                field=f"test_results[{index}]",
                rule="FEEDBACK-EXPORT-CONTRACT",
            )
        test_id = test_result.get("acceptance_test_id")
        if not isinstance(test_id, str) or test_id not in acceptance_by_id:
            raise FeedbackSignalExportError(
                f"acceptance test {test_id!r} cannot be resolved in the research project",
                field=f"test_results[{index}].acceptance_test_id",
                rule="FEEDBACK-EXPORT-REFERENCE",
                remediation="Reference an acceptance test declared by the research project.",
            )
        if test_id in seen_test_ids:
            raise FeedbackSignalExportError(
                f"acceptance test {test_id!r} appears more than once",
                field=f"test_results[{index}].acceptance_test_id",
                rule="FEEDBACK-EXPORT-CONTRACT",
                remediation="Export one signal per acceptance test.",
            )
        seen_test_ids.add(test_id)
        acceptance = acceptance_by_id[test_id]
        requirement_id = acceptance.get("target_requirement")
        if not isinstance(requirement_id, str) or requirement_id not in requirement_by_id:
            raise FeedbackSignalExportError(
                f"requirement {requirement_id!r} for acceptance test {test_id!r} cannot be resolved",
                field=f"acceptance_tests[{test_id}].target_requirement",
                rule="FEEDBACK-EXPORT-REFERENCE",
                remediation="Connect the acceptance test to an existing production requirement.",
            )
        requirement = requirement_by_id[requirement_id]
        acceptance_ids = requirement.get("acceptance_test_ids", [])
        if isinstance(acceptance_ids, list) and test_id not in acceptance_ids:
            raise FeedbackSignalExportError(
                f"requirement {requirement_id!r} does not reference acceptance test {test_id!r}",
                field=f"requirements[{requirement_id}].acceptance_test_ids",
                rule="FEEDBACK-EXPORT-REFERENCE",
                remediation="Make the acceptance test and requirement references agree before exporting.",
            )
        matched_observations = []
        for observation in observations:
            related = observation.get("related_requirement_ids", []) if isinstance(observation, dict) else []
            if isinstance(related, list) and requirement_id in related:
                matched_observations.append(
                    {
                        "id": observation.get("id"),
                        "statement": observation.get("statement"),
                        "method": observation.get("method"),
                        "limitations": observation.get("limitations"),
                        "related_requirement_ids": sorted(set(related)),
                    }
                )
        matched_observations.sort(key=lambda observation: str(observation.get("id", "")))
        signals.append(
            {
                "signal_id": f"RSE-{result['accepted_handoff']['research_project_id'].split('/', 1)[1]}-{result_id}-{test_id}",
                "acceptance_test_id": test_id,
                "requirement_ids": [requirement_id],
                "method": acceptance.get("method"),
                "pass_condition": acceptance.get("pass_condition"),
                "result": test_result.get("result"),
                "execution_conditions": test_result.get("conditions"),
                "presentation_conditions": None,
                "observations": matched_observations,
                "source_result_id": result_id,
                "source_result_sha256": result_hash,
            }
        )
    return sorted(signals, key=lambda signal: signal["signal_id"])


def _bundle_bytes(root: Path, target: str, result_id: str) -> tuple[dict[str, Any], bytes, bytes]:
    project = _project_path(root, target)
    result, audit, feedback_path = _load_imported_result(project, result_id)
    if result.get("accepted_handoff", {}).get("research_project_id") != target:
        raise FeedbackSignalExportError(
            "imported result does not target the requested research project",
            path=str(feedback_path),
            field="accepted_handoff.research_project_id",
            rule="FEEDBACK-EXPORT-REFERENCE",
            remediation="Export the result against its accepted research project.",
        )
    policy = _load_policy(root)
    try:
        validator, _, _ = _schema_validator(root, policy, None)
        _validate_schema(result, validator, policy.get("production_result_schema_versions", []), feedback_path)
    except ResultImportError as exc:
        raise FeedbackSignalExportError(
            str(exc),
            path=exc.path,
            field=exc.field,
            rule="FEEDBACK-EXPORT-CONTRACT",
            remediation="Re-import a production result that conforms to the pinned production-result schema.",
        ) from exc
    _privacy_scan(root, result, feedback_path, policy)
    signals = _source_records(project, result, result_id)
    findings = validate_repository(root, target)
    if findings:
        raise FeedbackSignalExportError(
            "research project is not valid before export: " + "; ".join(finding.render() for finding in findings),
            path=target,
            rule="FEEDBACK-EXPORT-RESEARCH-VALIDATION",
            remediation="Resolve project validation findings before exporting feedback signals.",
        )
    signals_bytes = b"".join(
        (json.dumps(signal, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for signal in signals
    )
    export_id = f"RSE-{target.split('/', 1)[1]}-{result_id}"
    manifest = {
        "schema_id": SCHEMA_ID,
        "export_id": export_id,
        "research_project_id": target,
        "production_project_id": result.get("production_project_id"),
        "result_id": result_id,
        "source_result_sha256": result_sha256(result),
        "signal_count": len(signals),
        "signals_sha256": _sha256_bytes(signals_bytes),
        "generated_at": result.get("generated_at"),
    }
    _, manifest_validator, signal_validator = _schema(root)
    _validate_instance(manifest, manifest_validator, path="manifest.json", rule="FEEDBACK-EXPORT-CONTRACT")
    for index, signal in enumerate(signals):
        _validate_instance(signal, signal_validator, path="signals.jsonl", rule="FEEDBACK-EXPORT-CONTRACT")
        if signal["source_result_sha256"] != audit.get("result_sha256"):
            raise FeedbackSignalExportError(
                "signal source hash does not match the import audit event",
                path="signals.jsonl",
                field=f"[{index}].source_result_sha256",
                rule="FEEDBACK-EXPORT-HASH",
                remediation="Restore the imported result and matching audit event.",
            )
    manifest_bytes = stable_json(manifest).encode("utf-8")
    return manifest, manifest_bytes, signals_bytes


def _validate_output_boundary(root: Path, output: Path) -> None:
    resolved = output.resolve()
    for name in ("projects", "data"):
        boundary = (root / name).resolve()
        if resolved == boundary or boundary in resolved.parents:
            raise FeedbackSignalExportError(
                "output must not materialize feedback artifacts inside canonical project or data directories",
                path=str(output),
                rule="FEEDBACK-EXPORT-BOUNDARY",
                remediation="Use an explicit external output directory or a temporary working-root export directory.",
            )


def export_feedback_signals(root: Path, target: str, result_id: str, output: Path) -> dict[str, Any]:
    root = root.resolve()
    if not RESULT_ID_PATTERN.fullmatch(result_id):
        raise FeedbackSignalExportError(
            "result_id must match PR followed by at least three digits",
            field="result_id",
            rule="FEEDBACK-EXPORT-REFERENCE",
            remediation="Use the imported production result ID, for example PR001.",
        )
    manifest, manifest_bytes, signals_bytes = _bundle_bytes(root, target, result_id)
    output = output.resolve()
    _validate_output_boundary(root, output)
    expected = {"manifest.json": manifest_bytes, "signals.jsonl": signals_bytes}
    if output.exists() or output.is_symlink():
        if output.is_symlink() or not output.is_dir():
            raise FeedbackSignalExportError(
                "output path exists and is not a bundle directory",
                path=str(output),
                rule="FEEDBACK-EXPORT-CONFLICT",
                remediation="Choose a new output directory or remove the conflicting path manually.",
            )
        names = {path.name for path in output.iterdir()}
        actual = {
            name: (output / name).read_bytes()
            for name in expected
            if (output / name).is_file()
        }
        if names == set(expected) and actual == expected:
            return {"status": "ALREADY_EXPORTED", "export_id": manifest["export_id"], "output_directory": str(output)}
        raise FeedbackSignalExportError(
            "existing bundle has different bytes or an unexpected file",
            path=str(output),
            rule="FEEDBACK-EXPORT-CONFLICT",
            remediation="Choose a new output directory; existing bundles are never overwritten.",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (temporary / "manifest.json").write_bytes(manifest_bytes)
        (temporary / "signals.jsonl").write_bytes(signals_bytes)
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"status": "EXPORTED", "export_id": manifest["export_id"], "output_directory": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export imported production feedback as a deterministic research-signal bundle.")
    parser.add_argument("target", help="project/<slug>")
    parser.add_argument("--result-id", required=True, help="imported production result ID, for example PR001")
    parser.add_argument("--output", type=Path, required=True, help="explicit output directory")
    parser.add_argument("--root", type=Path, required=True, help="temporary working root or external project root")
    args = parser.parse_args()
    try:
        print(stable_json(export_feedback_signals(args.root, args.target, args.result_id, args.output)), end="")
    except (FeedbackSignalExportError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
