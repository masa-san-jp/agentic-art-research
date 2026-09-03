#!/usr/bin/env python3
"""Scan completed project artifacts for repeated claims before production handoff.

The scanner consumes an explicit candidate project and an explicit history root.
It emits metadata-only references and scores; artifact text is never copied to the
report.  Applying a report to a project is an explicit ``--apply`` operation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping
import unicodedata

from _common import InputParseError, atomic_write_text, load_json, load_yaml, read_jsonl


CONTRACT_VERSION = "self-repetition-scan/v1"
CONTRACT_VERSION_V2 = "self-repetition-scan/v2"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]+$")
TEXT_FIELDS = {
    "claim",
    "claims",
    "proposition",
    "statement",
    "mechanism",
    "difference",
    "what_came_out",
    "field",
    "stands_against",
    "denies",
    "shown_not_told",
    "title",
}
SKIP_FIELDS = {"self_repetition_risk", "assessment", "mitigation", "risk_level", "reference"}
ARTIFACT_NAMES = {
    "03_knowledge/claims.jsonl",
    "04_decisions/production-hypotheses.yaml",
    "05_production/production-brief.yaml",
    "05_production/visual-language.yaml",
    "05_production/creative-direction.md",
    "03_plan/production-plan.yaml",
    "production-handoff/artifacts/creative-direction.md",
    "production-handoff/artifacts/production-hypotheses.yaml",
}
MARKER_START = "<!-- self-repetition-scan:start -->"
MARKER_END = "<!-- self-repetition-scan:end -->"


@dataclass(frozen=True)
class Signal:
    project_id: str
    source_ref: str
    signal_id: str
    text: str


@dataclass(frozen=True)
class ProjectSignals:
    project_id: str
    creator_id: str | None
    signals: tuple[Signal, ...]


class SelfRepetitionError(ValueError):
    """The scan input or application target cannot be accepted safely."""


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SelfRepetitionError("--now must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise SelfRepetitionError("--now must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_identity(repository: str, source_commit: str) -> None:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise SelfRepetitionError("repository must use owner/name format")
    if not SHA_PATTERN.fullmatch(source_commit):
        raise SelfRepetitionError("source_commit must be a 40-character lowercase Git SHA")


def _project_id(value: Any, fallback: str) -> str:
    candidate = value if isinstance(value, str) and value.strip() else fallback
    if not PROJECT_ID_PATTERN.fullmatch(candidate) or candidate in {".", ".."}:
        raise SelfRepetitionError(f"invalid project_id: {candidate!r}")
    return candidate


def _safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SelfRepetitionError(f"artifact escapes project root: {path}") from exc
    return relative.as_posix()


def _record_id(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _walk_text_fields(value: Any, path: tuple[str, ...] = (), inherited_id: str = "") -> Iterable[tuple[str, str, str]]:
    """Yield (signal id, field path, text) without exporting the text."""
    if isinstance(value, Mapping):
        local_id = _record_id(value.get("id"), inherited_id)
        for key, child in value.items():
            if not isinstance(key, str) or key in SKIP_FIELDS:
                continue
            child_path = path + (key,)
            if key in TEXT_FIELDS and isinstance(child, str) and child.strip():
                yield local_id or key, ".".join(child_path), child.strip()
            elif key not in {"id", "source_url", "url", "created_at", "updated_at", "reviewed_at"}:
                yield from _walk_text_fields(child, child_path, local_id)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_text_fields(child, path + (str(index),), inherited_id)


def _markdown_signals(text: str) -> Iterable[tuple[str, str, str]]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    for index, paragraph in enumerate(paragraphs, 1):
        content = "\n".join(line.strip() for line in paragraph.splitlines() if not line.lstrip().startswith("#"))
        content = re.sub(r"\s+", " ", content).strip(" -*")
        if len(content) >= 8:
            yield f"paragraph-{index}", f"paragraph-{index}", content


def _signals_from_file(path: Path, project_root: Path, project_id: str) -> list[Signal]:
    relative = _safe_relative(path, project_root)
    try:
        if path.name.endswith(".jsonl"):
            documents: Any = read_jsonl(path)
        elif path.suffix in {".yaml", ".yml"}:
            documents = load_yaml(path)
        elif path.suffix == ".md":
            documents = None
        else:
            return []
    except (OSError, InputParseError, ValueError) as exc:
        raise SelfRepetitionError(f"cannot read artifact {relative}: {exc}") from exc

    fields = _markdown_signals(path.read_text(encoding="utf-8")) if path.suffix == ".md" else _walk_text_fields(documents)
    signals: list[Signal] = []
    for ordinal, (record_id, field_path, text) in enumerate(fields, 1):
        signal_id = f"{record_id}@{field_path}" if record_id else f"signal-{ordinal}@{field_path}"
        source_ref = f"{project_id}::{relative}#{signal_id}"
        signals.append(Signal(project_id, source_ref, signal_id, text))
    return signals


def _artifact_paths(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for candidate in sorted(project_root.rglob("*")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.relative_to(project_root).as_posix()
        if relative in ARTIFACT_NAMES:
            paths.append(candidate)
    return paths


def _manifest_project_metadata(project_root: Path, fallback: str) -> tuple[str, str | None]:
    manifest = project_root / "manifest.yaml"
    if not manifest.is_file() or manifest.is_symlink():
        return _project_id(fallback, fallback), None
    try:
        document = load_yaml(manifest) or {}
    except (OSError, InputParseError, ValueError) as exc:
        raise SelfRepetitionError(f"cannot read manifest {manifest}: {exc}") from exc
    project = document.get("project") if isinstance(document, Mapping) else None
    value = project.get("id") if isinstance(project, Mapping) else None
    creator = project.get("creator_id") if isinstance(project, Mapping) else None
    if creator is not None and (not isinstance(creator, str) or not creator.strip()):
        creator = None
    return _project_id(value, fallback), creator


def _collect_project(project_root: Path, fallback_project_id: str) -> ProjectSignals:
    project_root = project_root.resolve()
    project_id, creator_id = _manifest_project_metadata(project_root, fallback_project_id)
    signals: list[Signal] = []
    for path in _artifact_paths(project_root):
        signals.extend(_signals_from_file(path, project_root, project_id))
    unique = {signal.source_ref: signal for signal in signals}
    return ProjectSignals(project_id, creator_id, tuple(unique[key] for key in sorted(unique)))


def _history_roots(history_root: Path) -> list[Path]:
    history_root = history_root.resolve()
    if not history_root.is_dir() or history_root.is_symlink():
        raise SelfRepetitionError("--history-root must be a real directory")
    artifact_filenames = {Path(name).name for name in ARTIFACT_NAMES}
    candidates = {
        path.parent
        for path in history_root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name in artifact_filenames
    }
    roots: set[Path] = set()
    for candidate in candidates:
        project_root = candidate
        while project_root != history_root:
            if (project_root / "manifest.yaml").is_file():
                break
            if any((project_root / directory).is_dir() for directory in ("05_production", "03_plan", "production-handoff")):
                break
            project_root = project_root.parent
        roots.add(project_root if project_root != history_root else candidate)
    return sorted(roots)


def _candidate_project(candidate: Path) -> ProjectSignals:
    candidate = candidate.resolve()
    if not candidate.is_dir() or candidate.is_symlink():
        raise SelfRepetitionError("--candidate must be a real project directory")
    fallback = candidate.name
    return _collect_project(candidate, fallback)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _tokens(value: str) -> set[str]:
    text = _normalized(value)
    tokens = set(re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)+|[a-z0-9]{3,}", text))
    for compound in tuple(tokens):
        if "-" in compound or "_" in compound:
            tokens.update(part for part in re.split(r"[-_]", compound) if len(part) >= 3)
    for sequence in re.findall(r"[一-龯ぁ-んァ-ヴー々]{2,}", text):
        tokens.add(sequence)
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def _ngrams(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", _normalized(value))
    return {compact[index : index + 3] for index in range(max(0, len(compact) - 2)) if compact[index : index + 3]}


def _similarity(left: str, right: str) -> tuple[float, list[str]]:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    common = left_tokens & right_tokens
    union = left_tokens | right_tokens
    word_score = len(common) / len(union) if union else 0.0
    gram_left = _ngrams(left)
    gram_right = _ngrams(right)
    gram_union = gram_left | gram_right
    gram_score = len(gram_left & gram_right) / len(gram_union) if gram_union else 0.0
    sequence_score = SequenceMatcher(None, _normalized(left), _normalized(right)).ratio()
    score = max(word_score, gram_score, sequence_score * 0.9)
    strong_terms = sorted(term for term in common if "-" in term or "_" in term or (term.isascii() and len(term) >= 6))
    if strong_terms:
        score = max(score, 0.9)
    return min(1.0, score), strong_terms[:8]


def _match_record(candidate: Signal, prior: Signal, score: float, terms: list[str]) -> dict[str, Any]:
    return {
        "candidate_signal_ref": candidate.source_ref,
        "prior_signal_ref": prior.source_ref,
        "score": round(score, 3),
        "matched_terms": terms,
    }


def _risk_level(matches: list[dict[str, Any]], project_count: int) -> str:
    project_scores: dict[str, float] = {}
    for match in matches:
        project = match["prior_signal_ref"].split("::", 1)[0]
        project_scores[project] = max(project_scores.get(project, 0.0), float(match["score"]))
    high = sum(score >= 0.9 for score in project_scores.values())
    if high >= 3 or max(project_scores.values(), default=0.0) >= 0.85 and project_count >= 2:
        return "HIGH"
    if len([score for score in project_scores.values() if score >= 0.35]) >= 2 or max(project_scores.values(), default=0.0) >= 0.6:
        return "MEDIUM"
    return "LOW"


def scan_projects(
    candidate: Path,
    history_root: Path,
    *,
    repository: str,
    source_commit: str,
    now: str,
    contract_version: str = CONTRACT_VERSION,
) -> dict[str, Any]:
    """Return a deterministic, metadata-only repetition report."""
    if contract_version not in {CONTRACT_VERSION, CONTRACT_VERSION_V2}:
        raise SelfRepetitionError(f"unsupported contract version: {contract_version}")
    _validate_identity(repository, source_commit)
    scanned_at = _timestamp(now)
    candidate_project = _candidate_project(candidate)
    if not candidate_project.signals:
        raise SelfRepetitionError("candidate project contains no supported claim, hypothesis, or mechanism signals")
    history_path = history_root.resolve()
    try:
        history_available = history_path.is_dir() and not history_path.is_symlink()
    except OSError:
        history_available = False
    if not history_available:
        if contract_version == CONTRACT_VERSION:
            raise SelfRepetitionError("--history-root must be a real directory")
        return {
            "contract_version": CONTRACT_VERSION_V2,
            "repository": repository,
            "source_commit": source_commit,
            "candidate_project_id": candidate_project.project_id,
            "scanned_at": scanned_at,
            "scanned_project_count": 0,
            "scanned_signal_count": 0,
            "candidate_signal_count": len(candidate_project.signals),
            "history_access": {"status": "UNAVAILABLE", "reason_code": "HISTORY_ROOT_UNAVAILABLE"},
            "risk_level": "UNKNOWN",
            "matches": [],
            "assessment": "過去プロジェクトの履歴rootを参照できないため、機構の重複を判定できない。",
            "mitigation": "履歴rootへのread-onlyアクセスを確保してから再走査し、UNKNOWNのままhandoffを確定しない。",
        }
    projects: list[ProjectSignals] = []
    for index, root in enumerate(_history_roots(history_root), 1):
        project = _collect_project(root, f"history-{index}")
        if (
            project.project_id != candidate_project.project_id
            and project.signals
            and (candidate_project.creator_id is None or project.creator_id == candidate_project.creator_id)
        ):
            projects.append(project)
    comparisons: list[dict[str, Any]] = []
    for candidate_signal in candidate_project.signals:
        best_by_project: dict[str, tuple[Signal, float, list[str]]] = {}
        for project in projects:
            best: tuple[Signal, float, list[str]] | None = None
            for prior_signal in project.signals:
                score, terms = _similarity(candidate_signal.text, prior_signal.text)
                if score >= 0.35 and (best is None or (score, prior_signal.source_ref) > (best[1], best[0].source_ref)):
                    best = (prior_signal, score, terms)
            if best is not None:
                best_by_project[project.project_id] = best
        for project_id in sorted(best_by_project):
            prior_signal, score, terms = best_by_project[project_id]
            comparisons.append(_match_record(candidate_signal, prior_signal, score, terms))
    comparisons.sort(key=lambda item: (-item["score"], item["prior_signal_ref"], item["candidate_signal_ref"]))
    risk_level = _risk_level(comparisons, len(projects))
    if comparisons:
        assessment = f"横断走査で{len(projects)}件中{len({item['prior_signal_ref'].split('::', 1)[0] for item in comparisons})}件の既存projectに類似signalを検出した。"
        mitigation = "重複したsignalの参照を確認し、差分をproduction hypothesisとcreative directionへ明記してからhandoffする。"
    else:
        assessment = f"横断走査した{len(projects)}件の既存projectに、候補signalと重複するsignalは検出されなかった。"
        mitigation = "新規候補を採用する場合も、次回handoff前に同じ履歴rootを再走査する。"
    report = {
        "contract_version": contract_version,
        "repository": repository,
        "source_commit": source_commit,
        "candidate_project_id": candidate_project.project_id,
        "scanned_at": scanned_at,
        "scanned_project_count": len(projects),
        "scanned_signal_count": sum(len(project.signals) for project in projects),
        "candidate_signal_count": len(candidate_project.signals),
        "risk_level": risk_level,
        "matches": comparisons,
        "assessment": assessment,
        "mitigation": mitigation,
    }
    if contract_version == CONTRACT_VERSION_V2:
        report["history_access"] = {"status": "AVAILABLE", "reason_code": None}
        # Preserve the schema's stable field order only at render time; the
        # report remains a normal mapping for callers.
    return report


def _render_report(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def apply_report(project: Path, report: Mapping[str, Any]) -> None:
    project = project.resolve()
    if not project.is_dir() or project.is_symlink():
        raise SelfRepetitionError("--project must be a real project directory")
    creative = project / "05_production" / "creative-direction.md"
    if not creative.is_file() or creative.is_symlink():
        raise SelfRepetitionError("project lacks 05_production/creative-direction.md")
    matches = report.get("matches") or []
    lines = [
        "## self_repetition_risk",
        "",
        MARKER_START,
        f"- risk_level: `{report['risk_level']}`",
        f"- scanned_projects: `{report['scanned_project_count']}`",
        f"- scanned_signals: `{report['scanned_signal_count']}`",
        f"- candidate_signals: `{report['candidate_signal_count']}`",
        f"- source: `{report['repository']}@{report['source_commit']}`",
        f"- reviewed_at: `{report['scanned_at']}`",
        "- prior_signal_refs:",
    ]
    if matches:
        for match in matches:
            lines.append(f"  - `{match['prior_signal_ref']}` (score `{match['score']:.3f}`)")
    else:
        lines.append("  - none")
    lines.extend([
        f"- assessment: {report['assessment']}",
        f"- mitigation: {report['mitigation']}",
        MARKER_END,
        "",
    ])
    block = "\n".join(lines)
    original = creative.read_text(encoding="utf-8")
    marker_pattern = re.compile(
        r"## self_repetition_risk\n\n" + re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?",
        re.DOTALL,
    )
    if marker_pattern.search(original):
        updated = marker_pattern.sub(block, original, count=1)
    else:
        separator = "\n" if original.endswith("\n") else "\n\n"
        updated = original + separator + block
    atomic_write_text(creative, updated)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--project", type=Path, help="project to receive the report when --apply is set")
    parser.add_argument("--apply", action="store_true", help="write the generated risk block to --project")
    parser.add_argument("--check", action="store_true", help="verify deterministic repeated output")
    parser.add_argument(
        "--contract-version",
        choices=(CONTRACT_VERSION, CONTRACT_VERSION_V2),
        default=CONTRACT_VERSION,
        help="select the versioned report contract; v2 records unavailable history explicitly",
    )
    args = parser.parse_args(argv)
    try:
        report = scan_projects(
            args.candidate,
            args.history_root,
            repository=args.repository,
            source_commit=args.source_commit,
            now=args.now,
            contract_version=args.contract_version,
        )
        rendered = _render_report(report)
        if args.check:
            repeated = scan_projects(
                args.candidate,
                args.history_root,
                repository=args.repository,
                source_commit=args.source_commit,
                now=args.now,
                contract_version=args.contract_version,
            )
            if rendered != _render_report(repeated):
                raise SelfRepetitionError("repeated scan output differs")
        if args.apply:
            if args.project is None:
                raise SelfRepetitionError("--apply requires --project")
            apply_report(args.project, report)
        elif args.project is not None:
            raise SelfRepetitionError("--project requires --apply")
        if args.output:
            atomic_write_text(args.output.resolve(), rendered)
        print(rendered, end="")
        return 0
    except (OSError, SelfRepetitionError, TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
