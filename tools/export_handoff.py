from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from _common import ROOT, load_yaml
from canonical import canonical_sha256
from handoff_common import HandoffInputError, HandoffSources, collection_yaml, load_handoff_sources, yaml_text
from validate import validate_repository


PROHIBITED_CLASSIFICATION_PATTERN = re.compile(r"(?<![A-Z0-9_])(?:PRIVATE_RAW|RESTRICTED)(?![A-Z0-9_])", re.IGNORECASE)
# A path that would only resolve on the machine that wrote the bundle. The
# leading ":" case exists to catch "key:/home/..." but an ARK identifier looks
# the same ("ark:/12148/..."), so a match inside a well-formed absolute URL is
# not a local path.
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'(=:])(?:/(?!/)\S+|[A-Za-z]:[\\/]\S*|~[\\/]\S*|file://\S+)",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def _local_path_hit(text: str) -> re.Match | None:
    spans = [match.span() for match in URL_PATTERN.finditer(text)]
    for match in ABSOLUTE_PATH_PATTERN.finditer(text):
        start, end = match.span()
        if any(begin <= start and end <= finish for begin, finish in spans):
            continue
        return match
    return None


class HandoffExportError(ValueError):
    """Raised when a handoff bundle cannot be exported safely."""


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _source_maps(sources: HandoffSources) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "decision": {str(record.get("id")): record for record in sources.decisions},
        "insight": {str(record.get("id")): record for record in sources.insights},
        "evidence": {str(record.get("id")): record for record in sources.evidence},
    }


def _summary(kind: str, record: dict[str, Any]) -> str:
    if kind == "evidence":
        source_type = record.get("source_type", "unknown-source")
        rights = record.get("rights_status", "unknown-rights")
        sensitivity = record.get("sensitivity", "unknown-sensitivity")
        return f"{source_type}; rights_status={rights}; sensitivity={sensitivity}"
    value = record.get("reason") if kind == "decision" else record.get("statement")
    if not isinstance(value, str) or not value.strip():
        value = record.get("selected_option") or record.get("question") or "No public summary recorded."
    return " ".join(str(value).split())[:280]


def _reference_categories(sources: HandoffSources) -> dict[str, list[str]]:
    """Which production question a reference answers.

    Production requires concept, visual and method to be answered by something,
    and only the study knows which of its records answers which. Unstated
    records fall to OTHER rather than being guessed.
    """
    path = sources.project / "05_production/reference-categories.yaml"
    if not path.is_file():
        return {}
    document = load_yaml(path) or {}
    mapping = document.get("categories") if isinstance(document, dict) else None
    if not isinstance(mapping, dict):
        raise HandoffExportError("reference-categories.yaml must map record IDs to a list of category IDs")
    result: dict[str, list[str]] = {}
    for record_id, values in mapping.items():
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            raise HandoffExportError(f"reference-categories.yaml entry {record_id!r} must be a list of category IDs")
        result[str(record_id)] = list(values)
    return result


def source_ref_index(sources: HandoffSources, handoff: dict[str, Any]) -> dict[str, Any]:
    maps = _source_maps(sources)
    categories = _reference_categories(sources)
    source_fields = {
        "decision_ids": ("decision", "04_decisions/decision-log.yaml"),
        "insight_ids": ("insight", "04_decisions/insight-register.yaml"),
        "evidence_ids": ("evidence", "02_evidence/evidence-ledger.jsonl"),
    }
    records: list[dict[str, Any]] = []
    source_refs = handoff.get("source_refs") if isinstance(handoff.get("source_refs"), dict) else {}
    for field, (kind, source_path) in source_fields.items():
        ids = source_refs.get(field, [])
        if not isinstance(ids, list):
            raise HandoffExportError(f"handoff source_refs.{field} must be a list")
        for record_id in ids:
            record = maps[kind].get(record_id)
            if record is None:
                raise HandoffExportError(f"handoff source_refs.{field} references missing {kind} {record_id!r}")
            if kind == "evidence" and record.get("sensitivity") in {"PRIVATE_RAW", "RESTRICTED"}:
                raise HandoffExportError(
                    f"handoff source_refs.{field} includes prohibited evidence classification {record.get('sensitivity')!r}"
                )
            entry = {
                "id": record_id,
                "kind": kind,
                "source_path": source_path,
                "record_hash": canonical_sha256(record),
                "summary": _summary(kind, record),
                "reference_categories": categories.get(record_id, ["OTHER"]),
            }
            # Production asks where a reference can be read. Evidence already
            # carries that; a decision or an insight lives in this repository
            # and has no external address, so it stays absent rather than
            # inventing one.
            location = record.get("source_location")
            if kind == "evidence" and isinstance(location, str) and location and not any(c.isspace() for c in location):
                parsed = urlsplit(location)
                # A source locator may be an opaque local catalog identifier.
                # Do not mislabel it as an externally accessible URL. Preserve
                # its canonical record hash/path and let the consumer report
                # absent external access without inventing a remote address.
                if (parsed.scheme == "https" and parsed.hostname and not parsed.username
                        and not parsed.password and not parsed.query and not parsed.fragment):
                    entry["access_url"] = location
            records.append(entry)
    return {"source_project": sources.project_id, "references": sorted(records, key=lambda item: (item["kind"], item["id"]))}


def _git_state(root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return not bool(result.stdout.strip())


def _policy(sources: HandoffSources) -> dict[str, Any]:
    path = sources.protocol_root / "config" / "handoff-policy.yaml"
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise HandoffExportError(f"{path}: handoff policy must be a mapping")
    return value


def _security_scan(files: dict[str, bytes], sources: HandoffSources) -> None:
    policy = _policy(sources)
    access_path = sources.protocol_root / "config" / "access-policy.yaml"
    access = load_yaml(access_path) or {}
    patterns = access.get("secret_patterns", []) if isinstance(access, dict) else []
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns:
        if not isinstance(pattern, dict) or not isinstance(pattern.get("id"), str) or not isinstance(pattern.get("pattern"), str):
            continue
        try:
            compiled.append((pattern["id"], re.compile(pattern["pattern"])))
        except re.error as exc:
            raise HandoffExportError(f"{access_path}: invalid secret pattern {pattern['id']!r}") from exc
    markers = [str(marker).lower() for marker in policy.get("signed_url_markers", []) if isinstance(marker, str)]
    for relative, raw in files.items():
        if relative.startswith("schemas/"):
            # Schema snapshots intentionally enumerate prohibited classifications
            # as contract vocabulary; they are not project payloads.
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HandoffExportError(f"bundle file is not UTF-8 text: {relative}") from exc
        if PROHIBITED_CLASSIFICATION_PATTERN.search(text):
            raise HandoffExportError(f"bundle file contains a prohibited classification: {relative}")
        if _local_path_hit(text):
            raise HandoffExportError(f"bundle file contains an absolute or local path: {relative}")
        lowered = text.lower()
        if any(marker in lowered for marker in markers):
            raise HandoffExportError(f"bundle file contains a signed URL marker: {relative}")
        for pattern_id, regex in compiled:
            if regex.search(text):
                raise HandoffExportError(f"bundle file matches secret pattern {pattern_id}: {relative}")


def _media_type(relative: str) -> str:
    if relative.endswith(".yaml"):
        return "application/yaml"
    if relative.endswith(".json"):
        return "application/json"
    if relative.endswith(".md"):
        return "text/markdown; charset=utf-8"
    raise HandoffExportError(f"unsupported bundle file type: {relative}")


def _role(relative: str) -> str:
    if relative == "production-handoff.yaml":
        return "HANDOFF"
    if relative == "artifacts/production-brief.yaml":
        return "PRODUCTION_BRIEF"
    if relative == "provenance.yaml":
        return "PROVENANCE"
    if relative.startswith("schemas/"):
        return "SCHEMA"
    return "ARTIFACT"


def _bundle_files(sources: HandoffSources, handoff: dict[str, Any]) -> dict[str, bytes]:
    project = sources.project
    creative_path = (project / "05_production" / "creative-direction.md").resolve()
    if project.resolve() not in creative_path.parents or not creative_path.is_file():
        raise HandoffExportError("05_production/creative-direction.md is required for the handoff bundle")
    schema_names = (
        "common.schema.json",
        "production-hypothesis.schema.json",
        "hypothesis-comparison.schema.json",
        "prototype-plan.schema.json",
        "visual-language.schema.json",
        "production-handoff.schema.json",
    )
    files: dict[str, bytes] = {
        "production-handoff.yaml": yaml_text(handoff).encode("utf-8"),
        "artifacts/production-hypotheses.yaml": collection_yaml(sources.hypotheses_document, "hypotheses").encode("utf-8"),
        "artifacts/hypothesis-comparison.yaml": collection_yaml(sources.comparison_document, "comparisons").encode("utf-8"),
        "artifacts/production-requirements.yaml": collection_yaml(sources.requirements_document, "requirements").encode("utf-8"),
        "artifacts/acceptance-tests.yaml": collection_yaml(sources.acceptance_tests_document, "acceptance_tests").encode("utf-8"),
        "artifacts/visual-language.yaml": yaml_text(sources.visual_language_document).encode("utf-8"),
        "artifacts/prototype-plans.yaml": collection_yaml(sources.prototype_document, "prototype_plans").encode("utf-8"),
        "artifacts/source-ref-index.yaml": yaml_text(source_ref_index(sources, handoff)).encode("utf-8"),
        "artifacts/creative-direction.md": creative_path.read_bytes(),
    }
    # The plan a person reads opens with what is being made, what it argues and
    # how it works. Only the study can write that, so it travels with the bundle.
    brief_path = (project / "05_production" / "production-brief.yaml").resolve()
    if project.resolve() not in brief_path.parents or not brief_path.is_file():
        raise HandoffExportError("05_production/production-brief.yaml is required for the handoff bundle")
    files["artifacts/production-brief.yaml"] = brief_path.read_bytes()
    for schema_name in schema_names:
        schema_path = sources.protocol_root / "schemas" / schema_name
        if not schema_path.is_file():
            raise HandoffExportError(f"schema snapshot is missing: {schema_path}")
        files[f"schemas/{schema_name}"] = schema_path.read_bytes()
    _security_scan(files, sources)
    return dict(sorted(files.items()))


def _provenance(sources: HandoffSources, handoff: dict[str, Any], files: dict[str, bytes], clean: bool) -> bytes:
    policy = _policy(sources)
    schema_bytes = files["schemas/production-handoff.schema.json"]
    value = {
        "source_repository": policy.get("source_repository", "masa-san-jp/agentic-art-research"),
        "source_commit": handoff.get("research_commit"),
        "source_tree_clean": clean,
        "source_schema": {
            "path": "schemas/production-handoff.schema.json",
            "version": handoff.get("schema_version"),
            "sha256": _sha256_bytes(schema_bytes),
        },
        "source_project": {
            "id": sources.project_id,
            "version": sources.project_data.get("version"),
        },
        "generator": {
            "name": "agentic-art-research/export_handoff",
            "version": policy.get("generator_version", "1.0.0"),
        },
        "canonicalization": policy.get("canonicalization", "json-sort-keys-compact-utf8-v1"),
        "generated_at": handoff.get("generated_at"),
    }
    return yaml_text(value).encode("utf-8")


def _manifest(handoff: dict[str, Any], files: dict[str, bytes], *, schema_version: str) -> bytes:
    entries = [
        {
            "path": relative,
            "role": _role(relative),
            "media_type": _media_type(relative),
            "size_bytes": len(raw),
            "sha256": _sha256_bytes(raw),
        }
        for relative, raw in sorted(files.items())
    ]
    file_set = canonical_sha256(
        [{"path": item["path"], "size_bytes": item["size_bytes"], "sha256": item["sha256"]} for item in entries]
    )
    value = {
        "bundle_schema_version": schema_version,
        "bundle_id": f"HB-{handoff['handoff_id']}-R{handoff['revision']}",
        "entrypoint": "production-handoff.yaml",
        "handoff_key": {"handoff_id": handoff["handoff_id"], "revision": handoff["revision"]},
        "files": entries,
        "integrity": {"file_set_sha256": file_set},
    }
    return yaml_text(value).encode("utf-8")


def _file_map(path: Path) -> dict[str, bytes]:
    if not path.is_dir() or path.is_symlink():
        raise HandoffExportError(f"export destination is not a regular directory: {path}")
    result: dict[str, bytes] = {}
    for candidate in path.rglob("*"):
        if candidate.is_symlink():
            raise HandoffExportError(f"export destination contains an unsafe object: {candidate}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise HandoffExportError(f"export destination contains an unsafe object: {candidate}")
        result[candidate.relative_to(path).as_posix()] = candidate.read_bytes()
    return result


def _is_generated_bundle(file_map: dict[str, bytes]) -> bool:
    required = {"manifest.yaml", "provenance.yaml", "production-handoff.yaml"}
    if not required.issubset(file_map):
        return False
    try:
        manifest = load_yaml_bytes(file_map["manifest.yaml"])
    except HandoffExportError:
        return False
    return (
        isinstance(manifest, dict)
        and manifest.get("entrypoint") == "production-handoff.yaml"
        and isinstance(manifest.get("bundle_id"), str)
        and manifest["bundle_id"].startswith("HB-")
    )


def load_yaml_bytes(raw: bytes) -> Any:
    try:
        value = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, OSError, yaml.YAMLError) as exc:
        raise HandoffExportError("existing export manifest is not valid UTF-8 YAML") from exc
    return value


def export_handoff(
    root: Path,
    target: str,
    output: Path,
    *,
    force: bool = False,
    allow_dirty: bool = False,
    protocol_root: Path | None = None,
    work_root: Path | None = None,
) -> Path:
    work = (work_root or root).resolve()
    sources = load_handoff_sources(work, target, protocol_root=protocol_root)
    findings = validate_repository(
        sources.root,
        f"project/{sources.project.name}",
        protocol_root=sources.protocol_root,
    )
    if findings:
        rendered = "\n".join(finding.render() for finding in findings)
        raise HandoffExportError(f"handoff export requires a cleanly validated project:\n{rendered}")
    output = output.resolve()
    if sources.project.resolve() == output or sources.project.resolve() in output.parents:
        raise HandoffExportError("export destination must not be inside the canonical project")
    clean = _git_state(sources.protocol_root)
    if clean is not True and not allow_dirty:
        raise HandoffExportError("source Git tree is not provably clean; commit the generator and sources or pass --allow-dirty for a fixture")
    clean_value = clean is True
    handoff = sources.handoff
    files = _bundle_files(sources, handoff)
    files["provenance.yaml"] = _provenance(sources, handoff, files, clean_value)
    files = dict(sorted(files.items()))
    files["manifest.yaml"] = _manifest(
        handoff,
        files,
        schema_version=str(_policy(sources).get("bundle_schema_version", "1.0.0")),
    )
    _security_scan(files, sources)
    expected = dict(sorted(files.items()))

    if output.exists():
        actual = _file_map(output)
        if actual == expected:
            return output
        if not force:
            raise HandoffExportError("export destination contains different bytes; use --force only after reviewing the destination")
        if not _is_generated_bundle(actual):
            raise HandoffExportError("--force is restricted to a previously generated handoff bundle")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for relative, raw in expected.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        if output.exists():
            if output.is_symlink() or not output.is_dir():
                raise HandoffExportError(f"export destination is not a replaceable directory: {output}")
            shutil.rmtree(output)
        os.replace(temporary, output)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a self-contained, deterministic production handoff bundle.")
    parser.add_argument("target", help="project/<slug>")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT, help="compatibility alias for --work-root")
    parser.add_argument("--work-root", type=Path, help="project work root")
    parser.add_argument("--protocol-root", type=Path, help="read-only protocol root for provenance")
    parser.add_argument("--force", action="store_true", help="replace a different generated destination after review")
    parser.add_argument("--allow-dirty", action="store_true", help="allow an uncommitted or fixture root; provenance records source_tree_clean=false")
    args = parser.parse_args()
    work_root = args.work_root or args.root
    try:
        path = export_handoff(
            work_root.resolve(),
            args.target,
            args.output,
            force=args.force,
            allow_dirty=args.allow_dirty,
            protocol_root=args.protocol_root.resolve() if args.protocol_root else None,
        )
    except (HandoffInputError, HandoffExportError, OSError) as exc:
        parser.error(str(exc))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
