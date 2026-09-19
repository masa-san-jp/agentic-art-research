from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import theme_research_pass as trp  # noqa: E402

PINNED = "b" * 40

# A stand-in for art-history-notes' tools/theme_research.py: it answers recon for two themes and
# validates candidates the way the owner does at the surface (statement + read proofs present).
STUB_OWNER_TOOL = textwrap.dedent('''
    import json, sys, hashlib
    argv = sys.argv[1:]
    if "--validate-candidate" in argv:
        cand = json.load(open(argv[argv.index("--validate-candidate") + 1], encoding="utf-8"))
        snaps = json.load(open(argv[argv.index("--source-snapshots") + 1])) if "--source-snapshots" in argv else {}
        errors = []
        if not cand["payload"]["statement"].strip(): errors.append("statement required")
        if not cand["payload"]["source_reads"]: errors.append("source reads required")
        for proof in cand["payload"]["source_reads"]:
            raw = open(snaps[proof["url"]], "rb").read()
            if hashlib.sha256(raw).hexdigest() != proof["content_sha256"]: errors.append("hash mismatch")
        if not cand["record"]["derived_from"]: errors.append("derived_from required")
        print(json.dumps({"ok": not errors, "errors": errors, "target_id": cand["payload"]["target_id"]}))
        sys.exit(0 if not errors else 1)
    themes = [argv[i + 1] for i, a in enumerate(argv) if a == "--theme"]
    results = []
    for t in themes:
        if t == "ムガル絵画":
            hits = [{"id": "movement/mughal-painting", "label_ja": "ムガル絵画", "label_en": "Mughal painting",
                     "type": "movement", "status": "verified", "n_sources": 12},
                    {"id": "movement/kowhaiwhai", "label_ja": "コーワイワイ", "label_en": None,
                     "type": "movement", "status": "verified", "n_sources": 14}]
            results.append({"theme": t, "hits": hits, "hit_count": 2, "exact_label_hits": ["movement/mughal-painting"]})
        elif t == "下書きだけ":
            hits = [{"id": "movement/draft-only", "label_ja": "下書きだけ", "label_en": None,
                     "type": "movement", "status": "draft", "n_sources": 1}]
            results.append({"theme": t, "hits": hits, "hit_count": 1, "exact_label_hits": ["movement/draft-only"]})
        else:
            results.append({"theme": t, "hits": [], "hit_count": 0, "exact_label_hits": []})
    print(json.dumps({"contract_version": "theme-research-recon/v1", "themes": themes, "results": results,
        "theme": themes[0], "hits": results[0]["hits"], "hit_count": results[0]["hit_count"],
        "coverage_grid": {"asia-south": {"18": 1}},
        "budget": {"contract_version": "theme-research-budget/v1", "per_run": {"max_passes": 1, "max_theme_terms": 4,
                   "max_candidates": 2, "max_source_fetches": 8, "wall_clock_seconds": 900}, "on_exhausted": "stop"},
        "next_step": "..."}, ensure_ascii=False))
''')


def _template(target_id: str) -> dict:
    payload = {"classification": "historical", "target_id": target_id, "project_id": "project/p1",
               "statement": "", "entity": None, "source_reads": [], "context_body": None}
    record = {"contract_version": "artifact-record/v1", "record_id": "theme-x", "revision": 1,
              "origin_instance_id": "instance-a", "creator_id": "creator-a", "owner_repository": "art-history-notes",
              "collection_id": "history-a", "kind": "art-history-knowledge",
              "payload_schema": "art-history-research-intake/v1",
              "payload_ref": "contexts/research-memory/payloads/deadbeef.json",
              "content_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                          separators=(",", ":")).encode()).hexdigest(),
              "sources": [], "derived_from": [], "epistemic_status": "externally-supported", "lifecycle": "candidate",
              "applicability": {"theme": "x"}, "rights": {"knowledge_write": True, "redistribute": False},
              "access_scope": "creator-private", "consent_ref": None, "created_at": "2026-09-19T00:00:00+00:00",
              "reviewed_at": None, "valid_until": None,
              "producer": {"kind": "agent", "generator_version": "stub", "code_commit": PINNED, "run_id": "run-1"},
              "supersedes": [], "invalidates": []}
    return {"candidate": {"record": record, "payload": payload}, "existing_target": None, "missing": []}


class ThemeResearchPassTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.owner = self.temp / "art-history-notes"
        (self.owner / "tools").mkdir(parents=True)
        (self.owner / "tools" / "theme_research.py").write_text(STUB_OWNER_TOOL, encoding="utf-8")
        self.work = self.temp / "work"
        self.work.mkdir()

    def write(self, name: str, value) -> Path:
        path = self.work / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def recon(self, *themes: str, name: str = "pass.json") -> dict:
        return trp.recon(art_history_root=self.owner, source_commit=PINNED, themes=list(themes),
                         python=sys.executable, output=self.work / name)

    # p2-ac1 ---------------------------------------------------------------
    def test_recon_pins_owner_commit_and_saves_contract(self) -> None:
        result = self.recon("ムガル絵画", "パタゴニア先住民美術")
        saved = json.loads((self.work / "pass.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, result)
        self.assertEqual(result["contract_version"], "theme-research-pass/v1")
        self.assertEqual(result["source_commit"], PINNED)
        self.assertEqual(result["recon"]["contract_version"], "theme-research-recon/v1")
        self.assertEqual(result["budget"]["max_candidates"], 2)
        self.assertEqual(result["decision"]["status"], trp.INVESTIGATE)
        self.assertEqual([u["theme"] for u in result["decision"]["uncovered"]], ["パタゴニア先住民美術"])

    def test_recon_refuses_commit_mismatch_output_inside_protocol_and_existing_output(self) -> None:
        with self.assertRaises(trp.PassError):
            trp.recon(art_history_root=self.owner, source_commit="not-a-sha", themes=["x"],
                      python=sys.executable, output=self.work / "a.json")
        with self.assertRaises(trp.PassError):
            trp.recon(art_history_root=self.owner, source_commit=PINNED, themes=["x"],
                      python=sys.executable, output=REPO_ROOT / "tests" / "should-not-exist.json")
        self.recon("x", name="dup.json")
        with self.assertRaises(trp.PassError):
            self.recon("x", name="dup.json")

    # p2-ac2 ---------------------------------------------------------------
    def test_verified_exact_hit_means_no_new_evidence_but_draft_does_not(self) -> None:
        covered = self.recon("ムガル絵画", name="covered.json")
        self.assertEqual(covered["decision"]["status"], trp.NO_NEW_EVIDENCE)
        job = trp.write_job(pass_path=self.work / "covered.json", candidate=None, snapshots=None,
                            exhausted=None, no_evidence_note=None, output=self.work / "job1.json")
        self.assertEqual(job["action"], "no-new-evidence")
        self.assertEqual(job["inputs"], {})
        self.assertIn("themes=ムガル絵画", job["reason"])
        self.assertIn("hit_counts=2", job["reason"])
        self.assertIn("movement/mughal-painting", job["reason"])
        draft = self.recon("下書きだけ", name="draft.json")
        self.assertEqual(draft["decision"]["status"], trp.INVESTIGATE)

    def test_investigate_without_candidate_needs_explicit_reason(self) -> None:
        self.recon("パタゴニア先住民美術", name="inv.json")
        with self.assertRaises(trp.PassError):
            trp.write_job(pass_path=self.work / "inv.json", candidate=None, snapshots=None,
                          exhausted=None, no_evidence_note=None, output=self.work / "j.json")
        job = trp.write_job(pass_path=self.work / "inv.json", candidate=None, snapshots=None, exhausted=None,
                            no_evidence_note="read 3 sources; none describes the theme", output=self.work / "j2.json")
        self.assertEqual(job["action"], "no-new-evidence")
        self.assertIn("read 3 sources", job["reason"])

    # p2-ac3 ---------------------------------------------------------------
    def assembled(self) -> tuple[Path, Path]:
        self.recon("パタゴニア先住民美術", name="inv.json")
        raw = b"Synthetic catalogue passage. Not an actual historical source."
        snapshot = self.work / "source.txt"
        snapshot.write_bytes(raw)
        snapshots = self.write("snapshots.json", {"https://example.org/synthetic": str(snapshot)})
        template = self.write("template.json", _template("movement/theme-x"))
        fill = self.write("fill.json", {"statement": "Synthetic observation.", "consent_ref": "consent/synthetic",
                                        "passages": [{"url": "https://example.org/synthetic", "locator": "p.1",
                                                      "start": 0, "end": 9}]})
        derived = self.write("derived.json", [{"origin_instance_id": "instance-a",
                                               "owner_repository": "agentic-art-research",
                                               "record_id": "research-record", "revision": 1}])
        result = trp.assemble(art_history_root=self.owner, source_commit=PINNED, pass_path=self.work / "inv.json",
                              template_path=template, fill_path=fill, snapshots_path=snapshots,
                              derived_from_path=derived, python=sys.executable, output=self.work / "candidate.json")
        self.assertTrue(result["owner_verdict"]["ok"])
        return self.work / "candidate.json", snapshots

    def test_assemble_computes_hashes_only_and_owner_validates(self) -> None:
        candidate_path, _ = self.assembled()
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        raw = (self.work / "source.txt").read_bytes()
        read = candidate["payload"]["source_reads"][0]
        self.assertEqual(read["content_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(read["slice_sha256"], hashlib.sha256(raw[0:9]).hexdigest())
        self.assertEqual(candidate["payload"]["statement"], "Synthetic observation.")
        self.assertEqual(candidate["record"]["consent_ref"], "consent/synthetic")
        self.assertEqual(candidate["record"]["derived_from"][0]["record_id"], "research-record")
        payload_hash = hashlib.sha256(json.dumps(candidate["payload"], sort_keys=True, ensure_ascii=False,
                                                 separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(candidate["record"]["content_sha256"], payload_hash)

    def test_assemble_rejects_unread_source_and_empty_statement(self) -> None:
        self.recon("パタゴニア先住民美術", name="inv.json")
        template = self.write("template.json", _template("movement/theme-x"))
        snapshots = self.write("snapshots.json", {})
        derived = self.write("derived.json", [{"origin_instance_id": "i", "owner_repository": "agentic-art-research",
                                               "record_id": "r", "revision": 1}])
        unread = self.write("fill-unread.json", {"statement": "claims a source it never read",
                                                 "passages": [{"url": "https://example.org/never", "locator": "p", "start": 0, "end": 1}]})
        with self.assertRaises(trp.PassError):
            trp.assemble(art_history_root=self.owner, source_commit=PINNED, pass_path=self.work / "inv.json",
                         template_path=template, fill_path=unread, snapshots_path=snapshots,
                         derived_from_path=derived, python=sys.executable, output=self.work / "c1.json")
        empty = self.write("fill-empty.json", {"statement": "   "})
        with self.assertRaises(trp.PassError):
            trp.assemble(art_history_root=self.owner, source_commit=PINNED, pass_path=self.work / "inv.json",
                         template_path=template, fill_path=empty, snapshots_path=snapshots,
                         derived_from_path=derived, python=sys.executable, output=self.work / "c2.json")
        self.assertFalse((self.work / "c1.json").exists())

    def test_write_job_carries_assembled_candidate(self) -> None:
        candidate_path, snapshots = self.assembled()
        job = trp.write_job(pass_path=self.work / "inv.json", candidate=candidate_path, snapshots=snapshots,
                            exhausted=None, no_evidence_note=None, output=self.work / "job.json")
        self.assertEqual(job["action"], "write")
        self.assertEqual(set(job["inputs"]), {"candidate", "source-snapshots"})
        self.assertIn("target=movement/theme-x", job["reason"])

    # p2-ac4 ---------------------------------------------------------------
    def test_budget_exhaustion_is_named_and_never_writes_partial_candidates(self) -> None:
        self.recon("パタゴニア先住民美術", name="inv.json")
        job = trp.write_job(pass_path=self.work / "inv.json", candidate=None, snapshots=None,
                            exhausted="max_source_fetches", no_evidence_note=None, output=self.work / "job.json")
        self.assertEqual(job["action"], "no-new-evidence")
        self.assertIn("BUDGET max_source_fetches=8", job["reason"])
        with self.assertRaises(trp.PassError):
            trp.write_job(pass_path=self.work / "inv.json", candidate=None, snapshots=None,
                          exhausted="not_a_limit", no_evidence_note=None, output=self.work / "job2.json")

    def test_cli_recon_roundtrip(self) -> None:
        import subprocess
        out = self.work / "cli-pass.json"
        completed = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "theme_research_pass.py"), "recon",
                                    "--art-history-root", str(self.owner), "--source-commit", PINNED,
                                    "--theme", "ムガル絵画", "--output", str(out)],
                                   capture_output=True, text=True, check=False, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["decision"]["status"], trp.NO_NEW_EVIDENCE)
        self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
