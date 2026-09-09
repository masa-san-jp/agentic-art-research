"""Curated owner knowledge in explicit Git stores; project/runtime remains external."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime

from _common import ROOT, load_yaml, read_jsonl
from build_graph import node_key
from validate import (_load_schema_validators, SCHEMA_FOR_JSONL,
                      SCHEMA_FOR_YAML_COLLECTION, validate_repository)
from export_feedback_signals import EMAIL_PATTERN, ACCOUNT_ID_PATTERN, PRIVATE_HOST_PATTERN

POLICY = load_yaml(ROOT / "config/research-memory.yaml")
OWNER = POLICY["owner"]
CONTRACT = POLICY["contract_version"]
FIELDS = set("contract_version record_id revision origin_instance_id creator_id owner_repository collection_id kind payload_schema payload_ref content_sha256 sources derived_from epistemic_status lifecycle applicability rights access_scope consent_ref created_at reviewed_at valid_until producer supersedes invalidates".split())


def _has_unsafe_store_symlink(path: Path) -> bool:
    """Reject store symlinks while tolerating macOS system path aliases."""
    macos_aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
        Path("/etc"): Path("/private/etc"),
    }
    for part in (path, *path.parents):
        if not part.is_symlink():
            continue
        if sys.platform == "darwin" and macos_aliases.get(part) == part.resolve(strict=False):
            continue
        return True
    return False


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def hashed(value):
    return hashlib.sha256(value).hexdigest()


def identity(record):
    return hashed(encoded([record[k] for k in ("origin_instance_id", "owner_repository", "record_id", "revision")]))


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("explicit timezone required")
    return result


def privacy_scan(value):
    """Reuse existing owner export privacy patterns without copying raw source text."""
    if isinstance(value, dict):
        for child in value.values():
            privacy_scan(child)
    elif isinstance(value, list):
        for child in value:
            privacy_scan(child)
    elif isinstance(value, str):
        if EMAIL_PATTERN.search(value) or ACCOUNT_ID_PATTERN.search(value) or PRIVATE_HOST_PATTERN.search(value):
            raise ValueError("identifying/private content in curated knowledge")
        security = load_yaml(ROOT / "config/access-policy.yaml")
        if any(re.search(row["pattern"], value) for row in security["secret_patterns"]):
            raise ValueError("secret in knowledge candidate")


def validate_payload(payload):
    if set(payload) != set(POLICY["payload_fields"]):
        raise ValueError("closed memory payload required; raw/runtime/profile fields forbidden")
    if not re.fullmatch(r"project/[a-z0-9]+(?:-[a-z0-9]+)*", payload["project_id"]):
        raise ValueError("invalid project identity")
    if payload["source_creator_id"] is not None and (not isinstance(payload["source_creator_id"], str) or not payload["source_creator_id"]):
        raise ValueError("source creator attribution must be explicit or unknown")
    if not re.fullmatch(r"[0-9a-f]{64}", payload["source_snapshot_sha256"]):
        raise ValueError("source snapshot hash required")
    findings = []
    validators = _load_schema_validators(ROOT, findings)
    if findings:
        raise ValueError("owner schema unavailable")
    if not isinstance(payload["items"], list) or not payload["items"]:
        raise ValueError("curated items required")
    ids = set()
    for item in payload["items"]:
        if set(item) != set(POLICY["item_fields"]) or item["kind"] not in POLICY["allowed_kinds"]:
            raise ValueError("unsupported owner kind or non-allowlisted fields")
        if item["kind"] == "question":
            data = item["data"]
            vocab = load_yaml(ROOT / "config/vocabularies.yaml")
            if set(data) != {"id", "text", "priority", "status"} or not re.fullmatch(r"Q\d{3,}", data["id"]) or not data["text"] or data["status"] not in vocab["question_statuses"] or data["priority"] not in {"mandatory", "optional"}:
                raise ValueError("invalid owner question")
            errors = []
        else:
            errors = list(validators[item["kind"]].iter_errors(item["data"]))
        if errors:
            raise ValueError("owner schema: " + errors[0].message)
        identifier = item["data"]["id"]
        if identifier in ids:
            raise ValueError("duplicate project record ID")
        ids.add(identifier)
        if item["rejection_code"] not in POLICY["rejection_codes"]:
            raise ValueError("unknown rejection code")
        if not isinstance(item["conditions"], dict) or not isinstance(item["reconsider_when"], dict):
            raise ValueError("explicit original and reconsideration conditions required")
        if item["kind"] == "rejected-option" and item["rejection_code"] == "none":
            raise ValueError("rejected option requires a reason code")
        data = item["data"]
        if item["kind"] == "evidence":
            if data["sensitivity"] not in {"PUBLIC_CITABLE", "PROJECT_INTERNAL"}:
                raise ValueError("profile/raw/restricted evidence is not authorized for this intake")
            if data["rights_status"] in {"unknown", "restricted"} or data["redistribution"] != "allowed":
                raise ValueError("unresolved redistribution rights")
    # Closed domain schemas exclude raw fields; the existing owner secret rules also scan strings.
    raw = encoded(payload).decode()
    privacy_scan(payload)
    security = load_yaml(ROOT / "config/access-policy.yaml")
    if any(re.search(row["pattern"], raw) for row in security["secret_patterns"]):
        raise ValueError("secret pattern in curated knowledge")
    if not isinstance(payload["reuse_trace"], list):
        raise ValueError("explicit reuse trace list required")
    for trace in payload["reuse_trace"]:
        if set(trace) != {"reference", "knowledge_commit", "item_id", "decision_id", "disposition", "reason", "effect", "common_trace"} or trace["decision_id"] not in ids or trace["disposition"] not in {"accepted", "rejected", "reconsidered"} or not trace["reason"] or not trace["effect"]:
            raise ValueError("reuse must identify an actual current decision, reason, and effect")
        common = trace["common_trace"]
        if not isinstance(common, dict) or set(common) != {"contract_version", "query", "selection_policy_version", "input_snapshot", "seen_scope", "records", "status"} or common["contract_version"] != "reuse-trace/v1" or common["status"] != "REUSED":
            raise ValueError("explicit common reuse-trace/v1 required")
        if not isinstance(common["query"], str) or not isinstance(common["selection_policy_version"], str) or not common["selection_policy_version"] or common["input_snapshot"] != {"project_id": payload["project_id"], "source_snapshot_sha256": payload["source_snapshot_sha256"], "knowledge_commit": trace["knowledge_commit"]}:
            raise ValueError("reuse query, policy and exact input snapshot required")
        if common["seen_scope"] != [trace["item_id"]] or not isinstance(common["records"], list) or len(common["records"]) != 1:
            raise ValueError("reuse must identify the reviewed item scope")
        row = common["records"][0]
        if set(row) != {"record_id", "revision", "owner", "decision", "reason", "affected", "origin_instance_id", "payload_ref", "content_sha256"}:
            raise ValueError("closed common reuse record required")
        expected = {"accepted": "adopted", "rejected": "rejected", "reconsidered": "revalidate"}[trace["disposition"]]
        ref = trace["reference"]
        if any(row[k] != ref[k] for k in ("record_id", "revision", "origin_instance_id")) or row["owner"] != ref["owner_repository"] or row["decision"] != expected or row["reason"] != trace["reason"] or row["affected"] != [trace["decision_id"]]:
            raise ValueError("common reuse trace differs from native decision")
    return payload


def capture(work_root, target, selections, reuse_trace=None):
    """Extract only selected schema records after the owner's complete-project validation."""
    work_root = Path(work_root).resolve()
    if work_root == ROOT or ROOT in work_root.parents:
        raise ValueError("actual project must remain outside protocol")
    failures = validate_repository(work_root, target, protocol_root=ROOT, work_root=work_root)
    if failures:
        raise ValueError("project owner validation failed: " + str(failures[0]))
    project = work_root / "projects" / target.removeprefix("project/")
    manifest = load_yaml(project / "manifest.yaml")
    if manifest["project"]["status"] not in {"COMPLETE", "COMPLETE_WITH_GAPS"}:
        raise ValueError("completed project required")
    records, snapshots = {}, {"manifest.yaml": hashed((project / "manifest.yaml").read_bytes())}
    for path, kind in SCHEMA_FOR_JSONL.items():
        file = project / path
        if kind not in POLICY["allowed_kinds"] or not file.exists():
            continue
        if file.is_symlink():
            raise ValueError("symlink source forbidden")
        snapshots[path] = hashed(file.read_bytes())
        for data in read_jsonl(file):
            records[data["id"]] = (kind, data)
    for path, (field, kind) in SCHEMA_FOR_YAML_COLLECTION.items():
        file = project / path
        if kind not in POLICY["allowed_kinds"] or not file.exists():
            continue
        if file.is_symlink():
            raise ValueError("symlink source forbidden")
        snapshots[path] = hashed(file.read_bytes())
        for data in (load_yaml(file) or {}).get(field, []):
            records[data["id"]] = (kind, data)
    visual_path = project / "05_production/visual-language.yaml"
    if visual_path.exists():
        if visual_path.is_symlink():
            raise ValueError("symlink source forbidden")
        snapshots["05_production/visual-language.yaml"] = hashed(visual_path.read_bytes())
        for mechanism in (load_yaml(visual_path) or {}).get("techniques", []):
            records[mechanism["id"]] = ("mechanism", mechanism)
    questions = project / "01_planning/question-register.yaml"
    if questions.is_symlink():
        raise ValueError("symlink question source forbidden")
    snapshots["01_planning/question-register.yaml"] = hashed(questions.read_bytes())
    for data in (load_yaml(questions) or {}).get("questions", []):
        records[data["id"]] = ("question", data)
    items = []
    for selection in selections:
        if set(selection) != {"id", "rejection_code", "conditions", "reconsider_when"}:
            raise ValueError("closed curation selection required")
        kind, data = records[selection["id"]]
        items.append({"kind": kind, "data": data, **{k: selection[k] for k in selection if k != "id"}})
    return validate_payload({"project_id": target, "source_creator_id": manifest["project"].get("creator_id"), "items": items, "source_snapshot_sha256": hashed(encoded(snapshots)), "reuse_trace": reuse_trace or []})


class MemoryStore:
    ref = "refs/heads/knowledge"

    def __init__(self, root, creator, collection, code_commit):
        self.root = Path(root)
        resolved_root = self.root.resolve(strict=False)
        protocol_root = ROOT.resolve(strict=False)
        if (
            not self.root.is_absolute()
            or _has_unsafe_store_symlink(self.root)
            or resolved_root == protocol_root
            or protocol_root in resolved_root.parents
        ):
            raise ValueError("explicit external nonsymlink owner store required")
        self.creator, self.collection, self.code_commit = creator, collection, code_commit
        if json.loads((self.root / "store.json").read_text()) != {"owner": OWNER, "creator": creator, "collection": collection}:
            raise ValueError("owner/creator/collection binding mismatch")
        if (self.root / "objects.git").is_symlink():
            raise ValueError("symlink Git store forbidden")
        actual = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        if actual != code_commit or subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"]):
            raise ValueError("execute a clean pinned code checkout")

    def git(self, *args, body=None, index=None):
        env = os.environ.copy()
        if index:
            env["GIT_INDEX_FILE"] = str(index)
        result = subprocess.run(["git", "--git-dir", str(self.root / "objects.git"),
            "-c", "user.name=Research owner", "-c", "user.email=research@example.invalid", *args],
            input=body, capture_output=True, env=env)
        if result.returncode:
            raise ValueError("owner Git operation failed: " + result.stderr.decode().strip())
        return result.stdout

    def head(self):
        return self.git("rev-parse", self.ref).decode().strip()

    def read(self, commit, path):
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("immutable knowledge commit required")
        paths = self.git("ls-tree", "-r", "--name-only", commit, "--", path).decode().splitlines()
        return self.git("show", commit + ":" + path) if path in paths else None

    def records(self, commit):
        paths = self.git("ls-tree", "-r", "--name-only", commit, "--", "knowledge/records/").decode().splitlines()
        return [json.loads(self.read(commit, p)) for p in paths]

    def validate(self, candidate):
        if set(candidate) != {"record", "payload"} or set(candidate["record"]) != FIELDS:
            raise ValueError("closed artifact-record/v1 envelope required")
        r, payload = candidate["record"], validate_payload(candidate["payload"])
        if (r["contract_version"], r["owner_repository"], r["creator_id"], r["collection_id"], r["kind"], r["payload_schema"]) != ("artifact-record/v1", OWNER, self.creator, self.collection, "research-memory", CONTRACT):
            raise ValueError("owner contract or creator mismatch")
        if type(r["revision"]) is not int or r["revision"] < 1:
            raise ValueError("positive revision required")
        for field in ("record_id", "origin_instance_id", "creator_id", "collection_id"):
            if not isinstance(r[field], str) or not r[field] or len(r[field]) > 200:
                raise ValueError("explicit stable identity required")
        if r["payload_ref"] != "knowledge/payloads/" + identity(r) + ".json" or r["content_sha256"] != hashed(encoded(payload)):
            raise ValueError("immutable payload path/hash mismatch")
        if r["access_scope"] not in {"public", "creator-private"} or r["rights"] != {"knowledge_write": True, "redistribute": r["access_scope"] == "public"} or r["rights"]["knowledge_write"] is not True:
            raise ValueError("explicit write and redistribution permission required")
        if r["access_scope"] == "creator-private" and not r["consent_ref"]:
            raise ValueError("consent reference required")
        if r["access_scope"] == "public" and any(item["kind"] == "evidence" and item["data"]["sensitivity"] != "PUBLIC_CITABLE" for item in payload["items"]):
            raise ValueError("internal evidence cannot be promoted to public knowledge")
        if r["epistemic_status"] not in {"observed", "externally-supported", "inferred", "proposed", "simulated", "unknown"} or r["lifecycle"] not in {"candidate", "accepted", "rejected", "superseded", "revoked"}:
            raise ValueError("unknown epistemic/lifecycle status")
        timestamp(r["created_at"])
        for field in ("reviewed_at", "valid_until"):
            if r[field] is not None:
                timestamp(r[field])
        producer = r["producer"]
        if set(producer) != {"kind", "generator_version", "code_commit", "run_id"} or producer["kind"] not in {"agent", "human", "tool"} or producer["code_commit"] != self.code_commit or not producer["run_id"]:
            raise ValueError("producer code/run provenance required")
        for field in ("sources", "derived_from", "supersedes", "invalidates"):
            if not isinstance(r[field], list):
                raise ValueError("reference list required")
        for ref in r["derived_from"] + r["supersedes"] + r["invalidates"]:
            if set(ref) != {"origin_instance_id", "owner_repository", "record_id", "revision"}:
                raise ValueError("qualified immutable revision reference required")
        return r

    def commit(self, candidate, parent, operation, run):
        head = self.head()
        operation_path = "knowledge/operations/" + hashed(operation.encode()) + ".json"
        saved = self.read(head, operation_path)
        fingerprint = hashed(encoded(candidate))
        if saved:
            ledger = json.loads(saved)
            if ledger != {"candidate_hash": fingerprint, "parent": parent}:
                raise ValueError("OPERATION_CONFLICT")
            commit = self.git("log", "-1", "--format=%H", head, "--", operation_path).decode().strip()
            return self.receipt(candidate["record"], parent, commit, operation, run, "ALREADY_APPLIED")
        r = self.validate(candidate)
        if parent != head:
            raise ValueError("PARENT_CONFLICT")
        prior = self.records(head)
        versions = [p["revision"] for p in prior if (p["origin_instance_id"], p["record_id"]) == (r["origin_instance_id"], r["record_id"])]
        if r["revision"] != max(versions, default=0) + 1:
            raise ValueError("REVISION_CONFLICT")
        if versions:
            previous = {"origin_instance_id": r["origin_instance_id"], "owner_repository": OWNER, "record_id": r["record_id"], "revision": max(versions)}
            if previous not in r["supersedes"]:
                raise ValueError("revision must explicitly supersede the preceding snapshot")
        known = {identity(p) for p in prior}
        for ref in r["derived_from"] + r["supersedes"] + r["invalidates"]:
            if ref["owner_repository"] == OWNER and identity(ref) not in known:
                raise ValueError("unresolved local revision dependency")
        for trace in candidate["payload"]["reuse_trace"]:
            if trace["reference"]["owner_repository"] != OWNER:
                raise ValueError("reuse trace must resolve this owner's stored revision")
            prior_record = self.read(trace["knowledge_commit"], "knowledge/records/" + identity(trace["reference"]) + ".json")
            if prior_record is None:
                raise ValueError("reuse reference absent from pinned knowledge")
            prior_record = json.loads(prior_record)
            row = trace["common_trace"]["records"][0]
            if any(row[k] != prior_record[k] for k in ("payload_ref", "content_sha256")):
                raise ValueError("reuse payload provenance mismatch")
            prior_payload = json.loads(self.read(trace["knowledge_commit"], prior_record["payload_ref"]))
            item = next((i for i in prior_payload["items"] if i["data"]["id"] == trace["item_id"]), None)
            _, invalid = self.active(head)
            if item is None or (trace["disposition"] != "rejected" and (prior_record["lifecycle"] == "rejected" or identity(prior_record) in invalid or item["rejection_code"] in POLICY["non_revivable_codes"] or item["data"].get("epistemic_status") == "REJECTED")):
                raise ValueError("unresolved or disallowed reuse")
            if trace["reference"] not in r["derived_from"]:
                raise ValueError("reuse dependency must propagate source corrections")
        writes = {"knowledge/records/" + identity(r) + ".json": encoded(r), r["payload_ref"]: encoded(candidate["payload"]),
                  operation_path: encoded({"candidate_hash": fingerprint, "parent": parent})}
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            index = Path(directory) / "index"
            self.git("read-tree", parent, index=index)
            for path, data in writes.items():
                blob = self.git("hash-object", "-w", "--stdin", body=data).decode().strip()
                self.git("update-index", "--add", "--cacheinfo", "100644," + blob + "," + path, index=index)
            tree = self.git("write-tree", index=index).decode().strip()
            commit = self.git("commit-tree", tree, "-p", parent, body=encoded({"operation": operation, "run": run})).decode().strip()
            self.git("update-ref", self.ref, commit, parent)
        return self.receipt(r, parent, commit, operation, run, "COMMITTED")

    def receipt(self, r, parent, commit, operation, run, status):
        return {"contract_version": "knowledge-write-receipt/v1", "operation_id": operation, "run_id": run,
            "owner": OWNER, "collection": self.collection, "target_parent": parent, "target_commit": commit,
            "accepted_ids": [r["record_id"]], "rejected_ids": [], "schema_version": CONTRACT, "policy_version": CONTRACT,
            "index_commit": None, "index_hash": None, "status": status, "reason": "Curated owner knowledge committed; index is resumable"}

    def active(self, commit):
        records = self.records(commit)
        invalid = {identity(r) for r in records if r["lifecycle"] in {"revoked", "superseded"}}
        for r in records:
            invalid.update(identity(ref) for ref in r["supersedes"] + r["invalidates"])
        while True:
            dependents = {identity(r) for r in records if any(identity(ref) in invalid for ref in r["derived_from"])}
            if dependents <= invalid:
                break
            invalid.update(dependents)
        latest = {}
        for r in records:
            key = (r["origin_instance_id"], r["record_id"])
            if key not in latest or latest[key]["revision"] < r["revision"]:
                latest[key] = r
        return list(latest.values()), invalid

    def retrieve(self, commit, query, conditions, at):
        latest, invalid = self.active(commit)
        hits = []
        for r in latest:
            if r["creator_id"] != self.creator or r["collection_id"] != self.collection:
                continue
            payload = json.loads(self.read(commit, r["payload_ref"]))
            if hashed(encoded(payload)) != r["content_sha256"]:
                raise ValueError("stored payload hash mismatch")
            for item in payload["items"]:
                if query.casefold() not in encoded(item["data"]).decode().casefold():
                    continue
                expired = r["valid_until"] is not None and timestamp(r["valid_until"]) <= timestamp(at)
                reason = item["rejection_code"]
                if identity(r) in invalid or expired:
                    disposition, rationale = "REVALIDATE", "source revision invalidated or validity expired"
                elif reason in POLICY["non_revivable_codes"] or r["lifecycle"] in {"rejected", "revoked", "superseded"} or item["data"].get("epistemic_status") == "REJECTED":
                    disposition, rationale = "REJECT", "prior negative evidence/rights/safety remains binding"
                elif reason != "none":
                    reconsider = item["reconsider_when"]
                    matched = bool(reconsider) and all(conditions.get(k) == v for k, v in reconsider.items())
                    disposition, rationale = ("RECONSIDER", "explicit changed conditions match prior reconsideration rule") if matched else ("REJECT", "original rejection conditions remain unresolved")
                elif all(conditions.get(k) == v for k, v in item["conditions"].items()) and r["lifecycle"] == "accepted":
                    disposition, rationale = "CANDIDATE", "applicable prior knowledge; agent must record a decision"
                else:
                    disposition, rationale = "REVALIDATE", "applicability not established"
                hits.append({"reference": {k:r[k] for k in ("origin_instance_id", "owner_repository", "record_id", "revision")},
                    "knowledge_commit": commit, "code_commit": self.code_commit, "project_id": payload["project_id"],
                    "item_id": item["data"]["id"], "kind": item["kind"], "data": item["data"], "source_creator_id": payload["source_creator_id"],
                    "disposition": disposition, "reason": rationale, "epistemic_status": r["epistemic_status"],
                    "additional_research": [] if disposition == "CANDIDATE" else [rationale]})
        return {"status": "FOUND" if hits else ("EMPTY_HISTORY" if not latest else "NOT_APPLICABLE"), "records": hits, "reuse_status": "NOT_RECORDED"}

    def index(self, commit):
        nodes, edges = {}, []
        for r in self.records(commit):
            payload = json.loads(self.read(commit, r["payload_ref"]))
            if hashed(encoded(payload)) != r["content_sha256"]:
                raise ValueError("stored payload hash mismatch")
            for item in payload["items"]:
                data = item["data"]
                key = identity(r) + ":" + node_key(payload["project_id"], data["id"])
                nodes[key] = {"kind": item["kind"], "project_id": payload["project_id"], "id": data["id"], "record_ref": identity(r)}
                for field, value in data.items():
                    if isinstance(value, list) and (field.endswith("_ids") or field in {"source_decisions", "supporting_claims", "opposing_claims"}):
                        for ref in value:
                            if isinstance(ref, str) and re.fullmatch(r"[A-Z]{2,4}\d{3,}", ref):
                                edges.append({"from": key, "to": identity(r) + ":" + node_key(payload["project_id"], ref), "relation": field})
        result = {"knowledge_commit": commit, "nodes": nodes, "edges": edges}
        path = self.root / "research-memory-index.json"
        if path.is_symlink():
            raise ValueError("symlink cache forbidden")
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as file:
            file.write(encoded(result)); temporary = Path(file.name)
        temporary.replace(path)
        return {"index_commit": commit, "index_hash": hashed(encoded(result))}

    def export_candidates(self, commit, candidates, owner, collection, operation, run):
        """Transport agent-authored owner payloads; only the destination can accept them."""
        if owner not in POLICY["candidate_owners"] or not candidates or not operation or not run:
            raise ValueError("explicit destination and operation/run required")
        known = {identity(r):r for r in self.records(commit)}
        records, payloads = [], {}
        for candidate in candidates:
            if set(candidate) != {"record", "payload"} or set(candidate["record"]) != FIELDS:
                raise ValueError("closed common owner candidate required")
            r = candidate["record"]
            if r["contract_version"] != "artifact-record/v1" or r["owner_repository"] != owner or r["creator_id"] != self.creator or r["collection_id"] != collection or r["lifecycle"] != "candidate":
                raise ValueError("destination, creator or candidate-only boundary mismatch")
            if r["content_sha256"] != hashed(encoded(candidate["payload"])):
                raise ValueError("owner payload hash mismatch")
            path = Path(r["payload_ref"])
            if path.is_absolute() or ".." in path.parts or "\\" in str(path):
                raise ValueError("unsafe owner payload reference")
            refs = [ref for ref in r["derived_from"] if ref["owner_repository"] == OWNER]
            if not refs or any(identity(ref) not in known for ref in refs):
                raise ValueError("candidate must derive from this pinned Research memory")
            if r["rights"].get("knowledge_write") is not True or r["access_scope"] != "creator-private" or not r["consent_ref"]:
                raise ValueError("candidate export requires explicit private owner consent")
            privacy_scan(candidate)
            if r["payload_ref"] in payloads and payloads[r["payload_ref"]] != candidate["payload"]:
                raise ValueError("conflicting destination payloads")
            records.append(r); payloads[r["payload_ref"]] = candidate["payload"]
        return {"status": "OWNER_VALIDATION_REQUIRED", "source_knowledge_commit": commit,
                "batch": {"operation_id": operation, "run_id": run, "owner": owner, "collection": collection, "records": records},
                "payloads": payloads}


def query_memory(query):
    if set(query) != {"store_root", "creator", "collection", "code_commit", "knowledge_commit", "query", "conditions", "at"}:
        raise ValueError("closed explicit memory query required")
    store = MemoryStore(query["store_root"], query["creator"], query["collection"], query["code_commit"])
    return store.retrieve(query["knowledge_commit"], query["query"], query["conditions"], query["at"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["capture", "validate", "commit", "index", "retrieve", "export-candidates"])
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--creator", required=True); parser.add_argument("--collection", required=True)
    parser.add_argument("--code-commit", required=True); parser.add_argument("--knowledge-commit", required=True)
    parser.add_argument("--candidate", type=Path); parser.add_argument("--operation-id"); parser.add_argument("--run-id")
    parser.add_argument("--query", default=""); parser.add_argument("--conditions", type=Path); parser.add_argument("--at")
    parser.add_argument("--work-root", type=Path); parser.add_argument("--project"); parser.add_argument("--selections", type=Path)
    parser.add_argument("--destination-owner"); parser.add_argument("--destination-collection")
    args = parser.parse_args()
    try:
        store = MemoryStore(args.store_root, args.creator, args.collection, args.code_commit)
        if args.command == "export-candidates":
            result = store.export_candidates(args.knowledge_commit, json.loads(args.candidate.read_text()),
                args.destination_owner, args.destination_collection, args.operation_id, args.run_id)
        elif args.command == "capture":
            result = capture(args.work_root, args.project, json.loads(args.selections.read_text()))
        elif args.command == "index":
            result = store.index(args.knowledge_commit)
        elif args.command == "retrieve":
            result = store.retrieve(args.knowledge_commit, args.query, json.loads(args.conditions.read_text()) if args.conditions else {}, args.at)
        else:
            candidate = json.loads(args.candidate.read_text())
            if args.command == "validate":
                store.validate(candidate); result = {"status": "VALID"}
            else:
                if not args.operation_id or not args.run_id:
                    raise ValueError("operation and run required")
                result = store.commit(candidate, args.knowledge_commit, args.operation_id, args.run_id)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        status = "INDEX_PENDING" if args.command == "index" else "CONFLICT" if str(exc) in {"OPERATION_CONFLICT", "PARENT_CONFLICT", "REVISION_CONFLICT"} else "REJECTED"
        print(json.dumps({"status": status, "target_commit": args.knowledge_commit, "reason": str(exc)})); return 1


if __name__ == "__main__":
    raise SystemExit(main())
