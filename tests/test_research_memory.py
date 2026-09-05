import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from research_memory import MemoryStore, capture, encoded, hashed, identity, CONTRACT, OWNER
from context_pack import build_context_pack
from new_project import create_project
from run_project import run_offline_fixture
from self_repetition import scan_projects
from next_action import build_next_action
from task_runtime import initialize_runtime

NOW = "2026-09-05T00:00:00Z"


class ResearchMemoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store_root = self.root / "memory"; self.store_root.mkdir()
        self.code = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        gitdir = self.store_root / "objects.git"
        subprocess.run(["git", "init", "--bare", "-q", str(gitdir)], check=True)
        (self.store_root / "store.json").write_text(json.dumps({"owner": OWNER, "creator": "creator-a", "collection": "research-a"}))
        tree = subprocess.check_output(["git", "--git-dir", str(gitdir), "mktree"], input=b"").decode().strip()
        commit = subprocess.check_output(["git", "--git-dir", str(gitdir), "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit-tree", tree], input=b"synthetic setup").decode().strip()
        subprocess.run(["git", "--git-dir", str(gitdir), "update-ref", "refs/heads/knowledge", commit], check=True)
        self.store = MemoryStore(self.store_root, "creator-a", "research-a", self.code)

    def payload(self):
        kinds = ["evidence", "claim", "insight", "decision", "requirement"]
        return {"project_id": "project/synthetic-first", "source_creator_id": "creator-a", "source_snapshot_sha256": "1" * 64, "reuse_trace": [],
            "items": [{"kind": kind, "data": json.loads((ROOT / "tests/fixtures/schema-valid" / (kind + ".json")).read_text()),
                "rejection_code": "none", "conditions": {}, "reconsider_when": {}} for kind in kinds]}

    def candidate(self, name="first", revision=1, payload=None):
        payload = payload or self.payload()
        r = {"contract_version": "artifact-record/v1", "record_id": name, "revision": revision,
            "origin_instance_id": "instance-a", "creator_id": "creator-a", "owner_repository": OWNER,
            "collection_id": "research-a", "kind": "research-memory", "payload_schema": CONTRACT,
            "payload_ref": "", "content_sha256": hashed(encoded(payload)), "sources": [], "derived_from": [],
            "epistemic_status": "proposed", "lifecycle": "accepted", "applicability": {},
            "rights": {"knowledge_write": True, "redistribute": False}, "access_scope": "creator-private", "consent_ref": "synthetic-consent",
            "created_at": NOW, "reviewed_at": None, "valid_until": None,
            "producer": {"kind": "agent", "generator_version": CONTRACT, "code_commit": self.code, "run_id": "synthetic"},
            "supersedes": [], "invalidates": []}
        r["payload_ref"] = "knowledge/payloads/" + identity(r) + ".json"
        return {"record": r, "payload": payload}

    def commit(self, candidate, operation="one"):
        return self.store.commit(candidate, self.store.head(), operation, "synthetic")

    def query(self, commit, **conditions):
        return {"store_root": str(self.store_root), "creator": "creator-a", "collection": "research-a", "code_commit": self.code,
                "knowledge_commit": commit, "query": "", "conditions": conditions, "at": NOW}

    def work_root(self):
        root = self.root / "work"; root.mkdir()
        for name in ("templates", "config", "schemas"):
            shutil.copytree(ROOT / name, root / name)
        (root / "projects").mkdir(); (root / "data").mkdir()
        return root

    def test_ac1_completed_project_capture_git_reopen_and_reverse_decision_links(self):
        root = self.work_root()
        run_offline_fixture(root, "harmony-study", ROOT / "tests/fixtures/harmony")
        selectors = [{"id": identifier, "rejection_code": "none", "conditions": {}, "reconsider_when": {}}
                     for identifier in ("EV001", "EV002", "CL001", "CL002", "IN001", "DC001", "DC002", "RQ001", "Q002")]
        payload = capture(root, "project/harmony-study", selectors)
        receipt = self.commit(self.candidate(payload=payload))
        reopened = MemoryStore(self.store_root, "creator-a", "research-a", self.code)
        reopened.index(receipt["target_commit"])
        index = json.loads((self.store_root / "research-memory-index.json").read_text())
        self.assertTrue(any(e["from"].endswith("::DC001") and e["to"].endswith("::EV001") for e in index["edges"]))
        self.assertTrue(any(e["from"].endswith("::RQ001") and e["to"].endswith("::DC001") for e in index["edges"]))
        self.assertIn("Q002", [h["item_id"] for h in reopened.retrieve(receipt["target_commit"], "", {}, NOW)["records"]])
        tracked = reopened.git("ls-tree", "-r", "--name-only", receipt["target_commit"]).decode()
        self.assertNotIn("07_runtime", tracked); self.assertNotIn("projects/", tracked)

    def test_ac2_second_project_context_and_explicit_decision_reuse_persist(self):
        first = self.candidate(); receipt = self.commit(first)
        root = self.work_root(); create_project(root, "second", "Second", "creator-a")
        initialize_runtime(root, "project/second", initialized_at=NOW)
        pack = build_context_pack(root, "project/second", "TASK001", "planner", memory_query=self.query(receipt["target_commit"]))
        self.assertEqual("NOT_RECORDED", pack["prior_knowledge"]["reuse_status"])
        action = build_next_action(root, "project/second", "synthetic-worker", NOW, dry_run=True,
            protocol_root=ROOT, work_root=root, memory_query=self.query(receipt["target_commit"]))
        self.assertEqual(pack["prior_knowledge"], action["context"]["prior_knowledge"])
        hit = next(h for h in pack["prior_knowledge"]["records"] if h["item_id"] == "CL001")
        payload = self.payload(); payload["project_id"] = "project/second"
        payload["items"][3]["data"]["selected_option"] = "Use a scale prototype to test the previously identified absence."
        payload["reuse_trace"] = [{"reference": hit["reference"], "knowledge_commit": receipt["target_commit"],
            "item_id": "CL001", "decision_id": "DC001", "disposition": "accepted", "reason": "Applicable prior observation",
            "effect": "DC001 selects the scale prototype based on CL001; installation-scale uncertainty stays unresolved."}]
        second = self.candidate("second", payload=payload); second["record"]["derived_from"] = [hit["reference"]]
        saved = self.commit(second, "second")
        data = json.loads(self.store.read(saved["target_commit"], second["record"]["payload_ref"]))
        self.assertEqual("accepted", data["reuse_trace"][0]["disposition"])
        self.assertIn("scale prototype", data["items"][3]["data"]["selected_option"])

    def test_ac3_budget_reconsideration_never_promotes_false_evidence(self):
        payload = self.payload()
        item = payload["items"][1]; item.update(rejection_code="budget-mismatch", conditions={"budget": "LOW"}, reconsider_when={"budget": "HIGH"})
        false_item = copy.deepcopy(item); false_item["data"]["id"] = "CL002"; false_item["rejection_code"] = "false-evidence"
        payload["items"].append(false_item)
        receipt = self.commit(self.candidate(payload=payload))
        low = {h["item_id"]:h for h in self.store.retrieve(receipt["target_commit"], "", {"budget": "LOW"}, NOW)["records"]}
        high = {h["item_id"]:h for h in self.store.retrieve(receipt["target_commit"], "", {"budget": "HIGH"}, NOW)["records"]}
        self.assertEqual("REJECT", low["CL001"]["disposition"])
        self.assertEqual("RECONSIDER", high["CL001"]["disposition"])
        self.assertEqual("REJECT", high["CL002"]["disposition"])

    def test_ac4_replay_conflict_correction_and_pending_index_recovery(self):
        candidate = self.candidate(); parent = self.store.head(); first = self.commit(candidate)
        self.assertEqual(first["target_commit"], self.store.commit(candidate, parent, "one", "synthetic")["target_commit"])
        with self.assertRaises(ValueError): self.store.commit(self.candidate("other"), parent, "two", "synthetic")
        ref = {k:candidate["record"][k] for k in ("origin_instance_id", "owner_repository", "record_id", "revision")}
        dependent = self.candidate("dependent"); dependent["record"]["derived_from"] = [ref]; self.commit(dependent, "dependent")
        revised = self.candidate(revision=2); revised["record"].update(lifecycle="revoked", supersedes=[ref])
        last = self.commit(revised, "revision")
        self.assertTrue(all(h["disposition"] == "REVALIDATE" for h in self.store.retrieve(last["target_commit"], "", {}, NOW)["records"]))
        self.assertTrue(all(h["disposition"] == "CANDIDATE" for h in self.store.retrieve(first["target_commit"], "", {}, NOW)["records"]))
        victim = self.root / "victim"; victim.write_text("unchanged")
        cache = self.store_root / "research-memory-index.json"; cache.symlink_to(victim)
        with self.assertRaises(ValueError): self.store.index(last["target_commit"])
        self.assertEqual(last["target_commit"], self.store.head()); self.assertEqual("unchanged", victim.read_text())
        cache.unlink(); self.store.index(last["target_commit"])
        cache.unlink(); self.store.index(last["target_commit"])

    def test_ac5_raw_secret_extra_fields_and_other_creator_rejected(self):
        with self.assertRaises(ValueError): MemoryStore(self.store_root, "creator-b", "research-a", self.code)
        for mutation in ("raw", "secret", "private", "path"):
            candidate = self.candidate()
            if mutation == "raw": candidate["payload"]["conversation"] = "not permitted"
            if mutation == "secret": candidate["payload"]["items"][1]["data"]["statement"] = "Bearer " + "a" * 24
            if mutation == "private": candidate["payload"]["items"][0]["data"]["sensitivity"] = "PRIVATE_RAW"
            candidate["record"]["content_sha256"] = hashed(encoded(candidate["payload"]))
            if mutation == "path": candidate["record"]["payload_ref"] = "../raw.json"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.commit(candidate)

    def test_memory_repetition_uses_existing_similarity_and_scoped_metadata(self):
        receipt = self.commit(self.candidate())
        root = self.work_root(); project = create_project(root, "second", "Second", "creator-a")
        data = self.payload()["items"][1]["data"]
        (project / "03_knowledge/claims.jsonl").write_text(json.dumps(data) + "\n")
        history = self.root / "empty-history"; history.mkdir()
        report = scan_projects(project, history, repository="masa-san-jp/agentic-art-research", source_commit=self.code,
            now=NOW, memory_query=self.query(receipt["target_commit"]))
        self.assertEqual(1, report["scanned_project_count"])
        self.assertTrue(report["matches"])
        self.assertIn(receipt["target_commit"], report["matches"][0]["prior_signal_ref"])

    def test_owner_candidate_export_preserves_payload_and_requires_owner_acceptance(self):
        first = self.candidate(); receipt = self.commit(first)
        ref = {k:first["record"][k] for k in ("origin_instance_id", "owner_repository", "record_id", "revision")}
        for owner in ("self-model-notes", "art-history-notes", "marketing-trends-notes"):
            candidate = self.candidate("candidate")
            candidate["record"].update(owner_repository=owner, collection_id="destination", lifecycle="candidate", derived_from=[ref],
                payload_ref="payloads/synthetic-candidate.json", payload_schema="synthetic-owner-input/v1", kind="synthetic-candidate")
            result = self.store.export_candidates(receipt["target_commit"], [candidate], owner, "destination", "export", "synthetic")
            self.assertEqual("OWNER_VALIDATION_REQUIRED", result["status"])
            self.assertEqual(candidate["payload"], result["payloads"]["payloads/synthetic-candidate.json"])
            self.assertEqual(candidate["record"], result["batch"]["records"][0])
            candidate["record"]["lifecycle"] = "accepted"
            with self.assertRaises(ValueError):
                self.store.export_candidates(receipt["target_commit"], [candidate], owner, "destination", "export", "synthetic")
        self.assertEqual(receipt["target_commit"], self.store.head())


if __name__ == "__main__": unittest.main()
