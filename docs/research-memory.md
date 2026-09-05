# Research memory

Every native decision reuse entry carries a closed `common_trace` conforming to
`reuse-trace/v1`: query, selection policy version, exact project/source/knowledge
snapshot, reviewed item scope, immutable retrieved artifact identity/hash and the
affected native decision. The owner checks this against the pinned stored payload;
a retrieval hit alone remains NOT_RECORDED. Rejected artifact lifecycle cannot be
adopted as accepted evidence. The rejection taxonomy distinguishes
evidence-contradicted, insufficient-evidence, budget, environment, timing and
artistic-choice; earlier owner reason codes remain readable.

Owner implementation of AAK-08 / Issue #93. Purpose and acceptance remain in
AAK-SPEC/PLAN at `b0e7c7f8d0a1f756fa708deef4fb380a62e45e0d`; this describes the implemented interface.

`config/research-memory.yaml` fixes the payload allowlist and rejection vocabulary.
Native evidence, observation, claim, contradiction, hypothesis, decision, rejected
option, uncertainty and requirement schemas remain authoritative. Questions retain
the existing register's ID/text/priority/status and vocabulary. Raw documents,
conversations, profile signals and runtime are not accepted payload kinds.

The owner uses the explicit AAK-04 store binding: `store.json` identifies owner,
creator and collection; `objects.git` is a bare repository with
`refs/heads/knowledge`. The caller supplies an absolute external root and immutable
code/knowledge commits. The executing protocol checkout must be clean at the code
commit. No remote is required or modified.

Each candidate contains the common closed `artifact-record/v1` envelope and one
`research-memory/v1` payload. Envelope kind is `research-memory`; payload_ref is
`knowledge/payloads/<sha256-of-qualified-record-identity>.json`. The hash input is
canonical JSON `[origin_instance_id, owner_repository, record_id, revision]`.
Content hashes use sorted UTF-8 JSON with separators `,` and `:`. The payload has
project_id, source_creator_id, source_snapshot_sha256, items and reuse_trace. Source
creator attribution is preserved separately from the active store's creator. An item contains kind,
native data, rejection_code, original conditions and reconsider_when. Code refs
and knowledge refs are independent. A new revision must supersede the preceding
snapshot explicitly; old commits remain readable.

`capture` validates a completed external project through the existing validator
and extracts only explicitly selected native records. A selection has id,
rejection_code, conditions and reconsider_when. It does not copy a project or raw
source. Include the supporting records needed for source→decision→requirement
reverse lookup; omitted references remain references to the external project.

```bash
.venv/bin/python tools/research_memory.py capture \
  --store-root "$MEMORY_ROOT" --creator "$CREATOR" --collection "$COLLECTION" \
  --code-commit "$CODE_COMMIT" --knowledge-commit "$KNOWLEDGE_COMMIT" \
  --work-root "$WORK_ROOT" --project project/example --selections "$SELECTIONS"
.venv/bin/python tools/research_memory.py commit \
  --store-root "$MEMORY_ROOT" --creator "$CREATOR" --collection "$COLLECTION" \
  --code-commit "$CODE_COMMIT" --knowledge-commit "$KNOWLEDGE_COMMIT" \
  --candidate "$CANDIDATE" --operation-id "$OPERATION" --run-id "$RUN"
.venv/bin/python tools/research_memory.py index \
  --store-root "$MEMORY_ROOT" --creator "$CREATOR" --collection "$COLLECTION" \
  --code-commit "$CODE_COMMIT" --knowledge-commit "$COMMITTED_KNOWLEDGE_COMMIT"
```

Commit validates the native payload and permission/creator boundary before an
atomic Git compare-and-swap. The operation ledger and record are in the same
commit. Replay returns the original commit; changed operation or stale parent
fails. Index failure returns INDEX_PENDING and the target commit. Retry index
against that commit; never repeat completed writes or discard their receipts.
Deleting the cache does not delete knowledge. The index retains revision-qualified
nodes and dependency edges, using the existing project-qualified node identity.

For retrieval, `context_pack.py`, `next_action.py` and `self_repetition.py` accept
`--memory-query` pointing to an external JSON object with exactly store_root,
creator, collection, code_commit, knowledge_commit, query, conditions and at.
The next-action query uses the same clock as its task. No missing history is
invented. Existing repetition comparison thresholds and unavailable-history
behavior remain in force. Retrieval returns CANDIDATE, REJECT, RECONSIDER or
REVALIDATE, with the applicable reason and additional research needed.

Budget/condition rejection can be reconsidered only when the explicit new
conditions match reconsider_when. False-evidence, unresolved rights and safety
remain rejected. A correction invalidates dependent revisions transitively;
their old snapshots stay available for audit, not current factual support.

Retrieval alone is NOT_RECORDED. A subsequent committed payload's reuse_trace
identifies the prior qualified revision/knowledge commit/item, the new decision
ID, disposition, reason and concrete effect. The prior revision must resolve,
must be a derived_from dependency, and disallowed evidence cannot be accepted.
The new native decision remains part of the payload. This makes the change in a
decision observable after restarting the store.

The declared tests use synthetic external Git stores and existing offline
fixtures. They are not evidence of a live provider, real Masa profile, physical
production or final AAK-02 integration. Owner-to-owner candidate export is a
separate boundary: Research cannot certify another owner's facts or profile.

`export-candidates` takes an external JSON list of agent-authored `{record,payload}`
candidates, `--destination-owner` and `--destination-collection`, plus the same
store/code/knowledge pins and operation/run IDs. The destination is Self Model,
Art History or Marketing. It emits the common batch (`operation_id`, `run_id`,
`owner`, `collection`, `records`) and hash-matched payloads. Each record must remain
`candidate`, be creator-private with explicit consent, and derive from a revision
in the pinned Research store. The output is OWNER_VALIDATION_REQUIRED. Payload
construction follows that destination's schema; this transport does not substitute
for its native prepare/validate/commit or count a destination write as successful.
The regression uses synthetic destination payloads and does not claim real-owner
acceptance; actual owner integration is deferred to AAK-02.
