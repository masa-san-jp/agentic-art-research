#!/usr/bin/env python3
"""Theme research pass (theme-research-pass/v1) — one bounded art-history investigation per run.

Owner-side spec: art-history-notes `docs/theme-research-cycle.md` (確定v1) §4 経路A, §5 R1/R3/R4.
Issue: agentic-art-research#112 (P2). This repository owns the *investigation*; art-history-notes
owns storage/validation; orchestration owns wiring. Nothing here pushes, opens issues, or writes
into either code checkout.

    .venv/bin/python tools/theme_research_pass.py recon \
        --art-history-root /abs/art-history-notes --source-commit <40-hex> \
        --theme "ムガル絵画" --theme "Mughal painting" --output /abs/work/theme-pass.json

    .venv/bin/python tools/theme_research_pass.py assemble \
        --art-history-root ... --source-commit ... --pass /abs/work/theme-pass.json \
        --template /abs/work/candidate-template.json --fill /abs/work/fill.json \
        --source-snapshots /abs/work/snapshots.json --derived-from /abs/work/derived.json \
        --output /abs/work/candidate.json

    .venv/bin/python tools/theme_research_pass.py write-job \
        --pass /abs/work/theme-pass.json [--candidate /abs/work/candidate.json \
        --source-snapshots /abs/work/snapshots.json | --exhausted max_candidates | --no-evidence-note "..."] \
        --output /abs/work/art-history-write-job.json

`recon` runs the owner's own `tools/theme_research.py --json` inside the pinned art-history checkout
(so the owner's demand log `data/queries.jsonl` records the search) and decides NO_NEW_EVIDENCE vs
INVESTIGATE. `assemble` fills an owner-issued candidate template from explicit agent inputs and
computes only hashes — it never invents a statement, a source, or a passage — then asks the owner to
validate (`theme_research.py --validate-candidate`). `write-job` emits the parent's owner write job
(`{action, inputs, reason}`); a `no-new-evidence` job carries the recon summary so "not searched"
and "searched, nothing found" stay distinguishable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from _common import ROOT, atomic_write_text, stable_json
from art_history_adapter import AdapterError, _source_commit

CONTRACT = "theme-research-pass/v1"
RECON_CONTRACT = "theme-research-recon/v1"
OWNER = "art-history-notes"
OWNER_TOOL = "tools/theme_research.py"
NO_NEW_EVIDENCE = "NO_NEW_EVIDENCE"
INVESTIGATE = "INVESTIGATE"


class PassError(ValueError):
    pass


def _external(path: Path, *, label: str, must_exist: bool) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise PassError(f"{label} must be an absolute external path: {path}")
    if path.is_symlink():
        raise PassError(f"{label} must not be a symbolic link: {path}")
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise PassError(f"{label} must stay outside the protocol checkout: {resolved}")
    if must_exist and not resolved.is_file():
        raise PassError(f"{label} not found: {resolved}")
    return resolved


def _new_output(path: Path) -> Path:
    resolved = _external(path, label="output", must_exist=False)
    if resolved.exists():
        raise PassError(f"output must be a new file: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(_external(path, label=label, must_exist=True).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PassError(f"{label} is not valid JSON: {exc.msg}") from exc


def _owner_root(root: Path, source_commit: str | None) -> tuple[Path, str]:
    root = Path(root).expanduser().resolve()
    if not (root / OWNER_TOOL).is_file():
        raise PassError(f"art-history-notes checkout without {OWNER_TOOL}: {root}")
    try:
        pinned = _source_commit(root, source_commit)
    except AdapterError as exc:
        raise PassError(str(exc)) from exc
    return root, pinned


def _run_owner_tool(python: str, root: Path, args: list[str], *, timeout: int = 300) -> tuple[int, str, str]:
    completed = subprocess.run(
        [python, OWNER_TOOL, *args], cwd=root, capture_output=True, text=True, timeout=timeout, check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


# ---------------------------------------------------------------- recon ----

def decide(recon: dict[str, Any]) -> dict[str, Any]:
    """NO_NEW_EVIDENCE only when every theme already has a verified entity whose label is the theme."""
    if recon.get("contract_version") != RECON_CONTRACT:
        raise PassError(f"owner recon must be {RECON_CONTRACT}")
    covered, uncovered = [], []
    for result in recon.get("results") or []:
        by_id = {hit["id"]: hit for hit in result.get("hits") or []}
        verified = [hid for hid in result.get("exact_label_hits") or []
                    if by_id.get(hid, {}).get("status") == "verified"]
        (covered if verified else uncovered).append(
            {"theme": result["theme"], "hit_count": result.get("hit_count", 0), "verified_targets": verified})
    if not covered and not uncovered:
        raise PassError("owner recon returned no themes")
    status = NO_NEW_EVIDENCE if not uncovered else INVESTIGATE
    summary = "; ".join(f"{c['theme']}→{','.join(c['verified_targets'])}" for c in covered) or "none"
    reason = (f"every theme already has a verified owner entity ({summary})" if status == NO_NEW_EVIDENCE
              else f"{len(uncovered)} theme(s) without a verified owner entity: "
                   + ", ".join(f"{u['theme']} (hits={u['hit_count']})" for u in uncovered))
    return {"status": status, "covered": covered, "uncovered": uncovered, "reason": reason}


def recon(*, art_history_root: Path, source_commit: str | None, themes: list[str], python: str,
          output: Path) -> dict[str, Any]:
    terms = [t.strip() for t in themes if t and t.strip()]
    if not terms:
        raise PassError("at least one --theme is required")
    out = _new_output(output)
    root, pinned = _owner_root(art_history_root, source_commit)
    args = ["--json"]
    for term in terms:
        args += ["--theme", term]
    code, stdout, stderr = _run_owner_tool(python, root, args)
    if code != 0:
        raise PassError(f"owner recon failed (exit {code}): {stderr.strip()[:500]}")
    try:
        owner_recon = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise PassError(f"owner recon did not return JSON: {exc.msg}") from exc
    decision = decide(owner_recon)
    budget = (owner_recon.get("budget") or {}).get("per_run") or {}
    if not budget:
        raise PassError("owner recon carried no budget; refusing to investigate without limits")
    result = {
        "contract_version": CONTRACT,
        "owner": OWNER,
        "source_commit": pinned,
        "themes": terms,
        "truncated_themes": owner_recon.get("truncated_themes") or [],
        "budget": budget,
        "decision": decision,
        "recon": owner_recon,
    }
    atomic_write_text(out, stable_json(result))
    return result


# ------------------------------------------------------------- assemble ----

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def assemble(*, art_history_root: Path, source_commit: str | None, pass_path: Path, template_path: Path,
             fill_path: Path, snapshots_path: Path, derived_from_path: Path, python: str,
             output: Path) -> dict[str, Any]:
    """Fill the owner template from explicit inputs; compute hashes only; owner validates."""
    out = _new_output(output)
    root, pinned = _owner_root(art_history_root, source_commit)
    pass_data = _read_json(pass_path, "pass")
    if pass_data.get("contract_version") != CONTRACT or pass_data.get("source_commit") != pinned:
        raise PassError("pass file must come from this pinned recon")
    if pass_data["decision"]["status"] != INVESTIGATE:
        raise PassError("assemble is only meaningful after an INVESTIGATE decision")
    template = _read_json(template_path, "template")
    candidate = template.get("candidate") if isinstance(template, dict) and "candidate" in template else template
    if not isinstance(candidate, dict) or set(candidate) != {"record", "payload"}:
        raise PassError("template must be an owner candidate template with record and payload")
    fill = _read_json(fill_path, "fill")
    allowed = {"statement", "entity", "classification", "context_body", "passages", "consent_ref", "access_scope"}
    if not isinstance(fill, dict) or set(fill) - allowed or not fill.get("statement", "").strip():
        raise PassError(f"fill must be an object with a non-empty statement and only {sorted(allowed)}")
    snapshots = _read_json(snapshots_path, "source-snapshots")
    if not isinstance(snapshots, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in snapshots.items()):
        raise PassError("source-snapshots must map URL → snapshot file path")
    derived = _read_json(derived_from_path, "derived-from")
    if not isinstance(derived, list) or not derived:
        raise PassError("derived-from must be a non-empty list of Research memory record references")
    for ref in derived:
        if not isinstance(ref, dict) or not all(k in ref for k in ("origin_instance_id", "owner_repository", "record_id", "revision")):
            raise PassError("each derived-from reference needs origin_instance_id, owner_repository, record_id, revision")

    payload = dict(candidate["payload"])
    payload["statement"] = fill["statement"]
    if "classification" in fill:
        payload["classification"] = fill["classification"]
    if "entity" in fill:
        payload["entity"] = fill["entity"]
    if "context_body" in fill:
        payload["context_body"] = fill["context_body"]
    reads = []
    for passage in fill.get("passages") or []:
        if not isinstance(passage, dict) or set(passage) != {"url", "locator", "start", "end"}:
            raise PassError("each passage needs exactly url, locator, start, end")
        snapshot = snapshots.get(passage["url"])
        if not snapshot:
            raise PassError(f"passage cites a URL without a snapshot: {passage['url']}")
        raw = _external(Path(snapshot), label="snapshot", must_exist=True).read_bytes()
        start, end = passage["start"], passage["end"]
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(raw):
            raise PassError(f"passage range invalid for {passage['url']}")
        reads.append({"url": passage["url"], "content_sha256": _sha(raw), "locator": passage["locator"],
                      "start": start, "end": end, "slice_sha256": _sha(raw[start:end])})
    payload["source_reads"] = reads

    record = dict(candidate["record"])
    record["derived_from"] = derived
    if "consent_ref" in fill:
        record["consent_ref"] = fill["consent_ref"]
    if "access_scope" in fill:
        record["access_scope"] = fill["access_scope"]
        record["rights"] = {"knowledge_write": True, "redistribute": fill["access_scope"] == "public"}
    record["content_sha256"] = _sha(_canonical(payload))
    assembled = {"record": record, "payload": payload}
    atomic_write_text(out, stable_json(assembled))

    code, stdout, stderr = _run_owner_tool(
        python, root, ["--validate-candidate", str(out), "--source-snapshots", str(_external(snapshots_path, label="source-snapshots", must_exist=True))])
    try:
        verdict = json.loads(stdout) if stdout.strip() else {"ok": False, "errors": [stderr.strip()[:500]]}
    except json.JSONDecodeError:
        verdict = {"ok": False, "errors": [stdout.strip()[:500] or stderr.strip()[:500]]}
    if code != 0 or not verdict.get("ok"):
        out.unlink(missing_ok=True)
        raise PassError("owner rejected the assembled candidate: " + "; ".join(verdict.get("errors") or ["unknown"]))
    return {"contract_version": CONTRACT, "candidate": str(out), "owner_verdict": verdict, "source_commit": pinned}


# ------------------------------------------------------------ write-job ----

def write_job(*, pass_path: Path, candidate: Path | None, snapshots: Path | None, exhausted: str | None,
              no_evidence_note: str | None, output: Path) -> dict[str, Any]:
    out = _new_output(output)
    pass_data = _read_json(pass_path, "pass")
    if pass_data.get("contract_version") != CONTRACT:
        raise PassError(f"pass file must be {CONTRACT}")
    decision = pass_data["decision"]
    summary = (f"{CONTRACT}: source_commit={pass_data['source_commit']}; themes="
               + ",".join(pass_data["themes"]) + "; hit_counts="
               + ",".join(str(r.get("hit_count", 0)) for r in pass_data["recon"].get("results") or []))
    if candidate is not None or snapshots is not None:
        if candidate is None or snapshots is None:
            raise PassError("a write job needs both --candidate and --source-snapshots")
        if decision["status"] != INVESTIGATE:
            raise PassError("a write job is only allowed after an INVESTIGATE decision")
        cand = _read_json(candidate, "candidate")
        if not isinstance(cand, dict) or set(cand) != {"record", "payload"} or not cand["payload"].get("statement", "").strip():
            raise PassError("candidate must be an assembled owner candidate with a statement")
        if isinstance(cand["payload"].get("entity"), dict) and cand["payload"]["entity"].get("status") == "verified":
            raise PassError("research never assigns verified")
        _read_json(snapshots, "source-snapshots")
        job = {"action": "write",
               "inputs": {"candidate": str(_external(candidate, label="candidate", must_exist=True)),
                          "source-snapshots": str(_external(snapshots, label="source-snapshots", must_exist=True))},
               "reason": summary + f"; candidate target={cand['payload']['target_id']}"}
    else:
        if decision["status"] == INVESTIGATE and not exhausted and not (no_evidence_note or "").strip():
            raise PassError("INVESTIGATE without a candidate needs --exhausted <limit> or --no-evidence-note")
        reason = summary + "; " + decision["reason"]
        if exhausted:
            if exhausted not in pass_data["budget"]:
                raise PassError(f"unknown budget limit: {exhausted}")
            reason += f"; BUDGET {exhausted}={pass_data['budget'][exhausted]}"
        if no_evidence_note:
            reason += "; " + no_evidence_note.strip()
        job = {"action": "no-new-evidence", "inputs": {}, "reason": reason}
    atomic_write_text(out, stable_json(job))
    return job


# ----------------------------------------------------------------- CLI ----

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["recon", "assemble", "write-job"])
    parser.add_argument("--art-history-root", type=Path)
    parser.add_argument("--source-commit", help="pinned art-history-notes commit; must match the checkout HEAD")
    parser.add_argument("--python", default=sys.executable, help="interpreter used to run the owner tool")
    parser.add_argument("--theme", action="append", default=[])
    parser.add_argument("--pass", dest="pass_path", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--fill", type=Path)
    parser.add_argument("--source-snapshots", type=Path)
    parser.add_argument("--derived-from", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--exhausted", help="budget limit name that stopped the pass (e.g. max_candidates)")
    parser.add_argument("--no-evidence-note", help="why an INVESTIGATE pass produced no candidate")
    parser.add_argument("--output", type=Path, required=True, help="new external JSON file")
    args = parser.parse_args()
    try:
        if args.command == "recon":
            if args.art_history_root is None:
                parser.error("recon requires --art-history-root")
            result = recon(art_history_root=args.art_history_root, source_commit=args.source_commit,
                           themes=args.theme, python=args.python, output=args.output)
        elif args.command == "assemble":
            missing = [n for n, v in (("--art-history-root", args.art_history_root), ("--pass", args.pass_path),
                                      ("--template", args.template), ("--fill", args.fill),
                                      ("--source-snapshots", args.source_snapshots),
                                      ("--derived-from", args.derived_from)) if v is None]
            if missing:
                parser.error("assemble requires " + ", ".join(missing))
            result = assemble(art_history_root=args.art_history_root, source_commit=args.source_commit,
                              pass_path=args.pass_path, template_path=args.template, fill_path=args.fill,
                              snapshots_path=args.source_snapshots, derived_from_path=args.derived_from,
                              python=args.python, output=args.output)
        else:
            if args.pass_path is None:
                parser.error("write-job requires --pass")
            result = write_job(pass_path=args.pass_path, candidate=args.candidate, snapshots=args.source_snapshots,
                               exhausted=args.exhausted, no_evidence_note=args.no_evidence_note, output=args.output)
    except (PassError, OSError, subprocess.TimeoutExpired, KeyError) as exc:
        parser.error(str(exc) or type(exc).__name__)
    print(stable_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
