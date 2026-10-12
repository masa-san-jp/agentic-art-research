# Issue #122 review — code verified, live NOT_RUN

Task `ELEMENT-RESEARCH-122`, branch `agent/122-element-research`. Review commits: `5f105c0a0c78ff1aa23e857e92736a43b800a9e9` (ten review fixes/default routing) and `b3f60fc33d913aa84a8e2dbcb2866d6428e4abaf` (migration/external validation documentation). Qualified code is b3f60fc, tree `6e935535bc1efe1112a83d7b42a219a64efec724`; base `ba1f2d2426073fd2dcb4d235fb5bf72d558de6ea`. Original five implementation commits and prior qualification are retained in PLANS.md. All commits are local; no push, PR or GitHub write.

All ten review requests are implemented: proposition and original research question in decision inputs; exact quotations of at least 20 characters with content-term or sentence-unit checks; distinct observation/claim derivations at 0.9; default research_elements routing with explicit deprecated legacy compatibility; strategy descriptions; deduplicated prior-work quotes; restored Japanese schema comment; scoped/shared-evidence claim pairs capped at config 120; distinct deterministic default fake questions/queries; documented external-project validation. Prior parent wire schemas remain verbatim at `337c33211d6048d934b2db0dcf749cd25cb2e617`; local mechanical extensions are documented in the provenance file. Config v2 rejects old frozen v1 checkpoints; finish those on pinned code or start fresh.

Full qualification on clean, unchanged code: shared interpreter Ran 410 tests in 1139.396s, OK, exit 0; repository-local interpreter Ran 410 tests in 1155.697s, OK, exit 0. Full commands used isolated TMPDIR roots and suppressed bytecode writes. Focused elements Ran 35 tests in 18.494s, OK; fixture matrix Ran 3 tests in 31.277s, OK. Compileall (114 Python files), both validators, both graph checks, security/docs/diff checks all passed (exit 0). Commands, paths, preliminary failures and log hashes are in PLANS.md and execution/state.yaml. Previous 400-test qualification remains historical evidence, not the current review count.

The unmodified default four-question fake CLI completed 118 first-attempt requests (106 element values, 12 searches; no retries). Counts: question 4, query 12, search 12, relevance 8, excerpt 8, observation 8, claim 8, claim-type 8, pair 16, insight 4, decision-question 4, option 8, adopt 4, reject 4, prior-work 2, difference 8. This synthetic fixture pins two unique sources and retains native quality INCOMPLETE and project_completed false. The old small-plan CLI smoke also passed again (24 requests). External validate.py --check passed for the generated default project; the focused native-gate test additionally proves altered external quotations fail with ELEMENT findings. Repository-only validation cannot exercise a Git-external project; use the documented --protocol-root / --work-root / --project command.

New research returns one element via next_action.py or harness.py run; respond through research_elements.py answer. Whole-role TASK001–009 execution requires request/plan research_route: legacy or an explicit CLI override and emits a warning. CLI overrides do not rewrite plans; repeat them on resume (the old harness resume command includes legacy). Remove this compatibility route after Stage B live acceptance and migration. Old acceptance/lease/production-brief gates are retained, with legacy fixture tests explicitly selecting their route.

Remaining acceptance: LIVE-ACCEPTANCE-NOT-RUN. This session forbids external site browsing and no owner Stage A proposition plus actual retrieved bodies was provided as a live input. Next start: orchestrator reviews local commits, creates a fresh Git-external project from the owner's central proposition, answers each research_elements CLI request with real inference/public retrieval, verifies grounded claims and decisions in the pinned ledger, and records owner output acceptance. Keep the task BLOCKED until that evidence exists. Parent run.py integration and Stage C are separate issues.

No sensitive information or real project/profile/runtime/raw was placed in Git; no actual graph was retained in data/. Synthetic CLI/element/full-suite project directories were cleaned normally; isolated full-suite temporary roots retain only empty runtime locks. OS-temp cleanup of the earlier interrupted legacy test remains unverified. No real/public external artifact or production output was produced.

# AAK02 native authoring/export repair qualified

Research code2304261b0c009b9f8690f2d5afbd76a53304c8a9 passed313 fulltests and focused55/25. Native role boundary, source URI and generated snapshot conflicts are repaired; PR97 is separate from parent197 live acceptance. Active live code checkouts at465/230 remain untouched; this clone records submission evidence only. Parent agents continue final-one/two pairs. No merge, raw publication or physical execution.

# AAK02 native authoring repair

Live external agents found required production-brief.yaml omitted from role write targets. Isolated follow-up to qualified AAK09; no active code checkout or user data changed. Add only existing canonical brief and external-reference targets to their proper roles, preserve typed gates and forbidden runtime paths, qualify and publish separately. Parent197 is the integration acceptance authority.

# AAK09 qualified — 2026-09-08

Code `046c2380310d208ca52cbca461c95c798328b599`, base c3e25347b3f36d96e903690ff0274838333f5132, branch agent/aak-09-cumulative-specificity. 5 synthetic AC / 9 focused / 307 full tests and native gates PASS. Evidence execution/aak-09-verification.json. No real project/profile/raw was placed in protocol Git. Main and AAK02 integration are NOT_RUN. Parent next task: Production AAK10, reuse qualified Production60 PR63.

# AAK09 in progress — 2026-09-08

Task AAK-09, Issue94, branch agent/aak-09-cumulative-specificity, base c3e25347b3f36d96e903690ff0274838333f5132. Dependencies and evidence in PLANS.md. Implement cumulative specificity/native context and validation integration; then tests.test_cumulative_specificity, validator and full suite. No actual project/profile/raw is written to protocol Git.

# AAK-08 verified handoff

Latest qualification: local `b67d777422960445e33199cf37e636ed71e4a2eb`, remote
`13c819eb34e91198c3584b8cfd75c81494ab148c`, identical tree
`1a052301f312b22d5e0d67410281bcc0f761c9b6`. This supersedes the earlier code
qualification below. The common trace now binds query/policy, input snapshot,
seen item scope, artifact revision/payload hash and affected native decision.
Required rejection reasons are distinct. Rejected artifact lifecycle cannot be
adopted; stale parent/operation/revision failures report CONFLICT.

Focused 7 PASS; full 298 PASS in 187.227s; validator/docs/diff PASS. Full log SHA-256
`3901b48a974049bc4c6ebf01f3feba05ec854d8e6c4299e23d16e3c4213491e8`.
An intermediate full run had one state/queue synchronization failure; it was
repaired and the full suite rerun. No test or gate was weakened.

Latest retained synthetic knowledge: first `74e3518df8e2c69d281b80a5dc07b72f2194f3cd`,
second `7188bc56daa900455d4cf757b91d901611703238`. The second project's DC001 chooses
a scale prototype using first/CL001. Its reuse-trace/v1 has query empty (all),
selection_policy_version research-memory/v1, seen_scope [CL001], affected [DC001],
decision adopted, reason Applicable prior observation, source artifact first@1,
origin instance-a, payload SHA-256
`85dcd310773c11f47f37e5276506ca5f94d26e0893273f66e40c36de362b8242`.
Source snapshot of repeated `1` digits is explicitly synthetic fixture metadata;
it is not a real project hash or live-agent evidence. AC1 separately tests actual
native project capture and hashing. Full payload and trace are retained in the
external synthetic owner store, with the same restart/reload test exercised.

AAK-12 is now submitted as viewer-response-notes PR7. Next eligible prerequisite:
Production #60 for Project #6/AAK-13. AAK-09 and live AAK-02 remain uncompleted.

The following older proof is historical evidence for the preceding candidate:

Task AAK-08 / Issue #93 / branch agent/aak-08-research-memory.
Base 71bfec77ea0d189186b8248565d340c124b81eab; SSOT pin b0e7c7f8d0a1f756fa708deef4fb380a62e45e0d.
AAK-04 qualified candidate and checks are in PLANS.md. Code and all five synthetic acceptance items PASS. No merge or live integration has been performed.
Next: AAK-09 after AAK-05/07 owner gates; parent can continue independent AAK-12. No production plan or live-agent reuse has been generated. The following is evidence, not a third specification.

```json
{
  "task": "AAK-08",
  "mode": "synthetic-owner-fixture",
  "code_commit": "ec6b9eba5b2597368c5cfd8eaef6a611753a6f21",
  "receipts": [
    {
      "contract_version": "knowledge-write-receipt/v1",
      "operation_id": "one",
      "run_id": "synthetic",
      "owner": "agentic-art-research",
      "collection": "research-a",
      "target_parent": "1f31e6665c4561b170603576f4dfc8644e24b6d2",
      "target_commit": "694b50042d9f97db834ef6fbf8cf819ee316d8ac",
      "accepted_ids": [
        "first"
      ],
      "rejected_ids": [],
      "schema_version": "research-memory/v1",
      "policy_version": "research-memory/v1",
      "index_commit": null,
      "index_hash": null,
      "status": "COMMITTED",
      "reason": "Curated owner knowledge committed; index is resumable"
    },
    {
      "contract_version": "knowledge-write-receipt/v1",
      "operation_id": "second",
      "run_id": "synthetic",
      "owner": "agentic-art-research",
      "collection": "research-a",
      "target_parent": "694b50042d9f97db834ef6fbf8cf819ee316d8ac",
      "target_commit": "904c92f94e8ce55159e079625c5b7a4b9ef4d075",
      "accepted_ids": [
        "second"
      ],
      "rejected_ids": [],
      "schema_version": "research-memory/v1",
      "policy_version": "research-memory/v1",
      "index_commit": null,
      "index_hash": null,
      "status": "COMMITTED",
      "reason": "Curated owner knowledge committed; index is resumable"
    }
  ],
  "index": {
    "index_commit": "904c92f94e8ce55159e079625c5b7a4b9ef4d075",
    "index_hash": "01d0f2873e105419ad85113e20cc9fd3ef522961ea2d93c5654aa67a1a95f598"
  },
  "reuse_trace": [
    {
      "reference": {
        "origin_instance_id": "instance-a",
        "owner_repository": "agentic-art-research",
        "record_id": "first",
        "revision": 1
      },
      "knowledge_commit": "694b50042d9f97db834ef6fbf8cf819ee316d8ac",
      "item_id": "CL001",
      "decision_id": "DC001",
      "disposition": "accepted",
      "reason": "Applicable prior observation",
      "effect": "DC001 selects scale prototype from prior CL001; installation uncertainty retained."
    }
  ],
  "knowledge_locator": "synthetic-local-git://aak-08-evidence/synthetic-memory",
  "live_agent_integration": "NOT_RUN",
  "production_plan": "NOT_RUN",
  "github_code_commit": "a8c859a810b7b55de775a0f501e3455732c1fd65",
  "exact_code_tree": "48433ed294fb3abc100e3fa5508585c8a5208284",
  "acceptance": {
    "AAK-08-AC1": "PASS: synthetic owner acceptance",
    "AAK-08-AC2": "PASS: synthetic owner acceptance",
    "AAK-08-AC3": "PASS: synthetic owner acceptance",
    "AAK-08-AC4": "PASS: synthetic owner acceptance",
    "AAK-08-AC5": "PASS: synthetic owner acceptance"
  },
  "verification": {
    "focused": "7 PASS, 3.452s",
    "full": "298 PASS, 186.965s",
    "validator": "PASS",
    "docs": "PASS",
    "diff": "PASS",
    "focused_sha256": "004957db9fd8e3f602ab740ab4298daa51153f32c40fe198a01a1337f160cfa2",
    "full_sha256": "815a880c0e7298e4fa4925219d429a181f838195a960138c97a221798ed40888"
  }
}
```

## Issue #133 / WORKS-HISTORY-133 (2026-10-13 JST)

Branch `agent/133-selfrep-history`; Research base/source commit
`0fa1509a2aef991a355665a912219d2cfde0f393`. Catalog source commit
`8b023ac200d55aca00a6c283c85c95f478ea7da6`, works index SHA-256
`5e08ec7a5d35b7866c1f5dca863f0998f8073209cb099f91a15c195daa06bb9f`.
Changed paths and restart procedure are recorded in execution/state.yaml.

The deterministic adapter only reads indexed work Markdown and explicit creator
metadata. It writes immutable minimal scanner projects outside Git, plus relative
source paths/hashes. Synthetic integration generates one Stage B review and removes
QUALITY-REQUIRED-SELF_REPETITION_REVIEW. Focused command
`.venv/bin/python -m unittest discover -s tests -p test_works_history.py -v`:
10 tests PASS. Preliminary focused run failed on the test helper's backwards clock;
fixture clock and duplicate imported-test discovery were corrected before qualification.
Full unittest and final validator evidence follow below.

Real verification ran `tools/works_history.py --works-root <owner-catalog>
--output <external-history>` and the exact Stage B scanner path: candidate contains
only frozen manifest + generated claims, `self-repetition-scan/v2`, repository identity,
frozen protocol commit and last_now. A copy under the external verification root was
used for `Engine.finish` in memory; neither original nor copied checkpoint was rewritten.
The original project's complete file-hash set was unchanged.

Result for `project/auto-live-20261012-a9-2a76271f9c04`: 20 candidate signals,
0 comparable projects, 0 historical signals, 0 matches. Native scanner reports LOW
with available empty history; Stage B correctly normalizes it to UNKNOWN and emits
0 reviews. QUALITY-REQUIRED-SELF_REPETITION_REVIEW remains (along with existing
minimum-evidence and minimum-requirements failures). The owner's works index is empty;
plans were deliberately not substituted for produced works. Task remains BLOCKED on
NO-INDEXED-WORKS, although adapter code and synthetic acceptance are implemented.

Next start: supply indexed actual works with explicit creator identity, build a fresh
external history root, then pin it on the first next of a new Stage B run. Existing
frozen runs cannot replace their history. Stage C mechanism review remains separate.
No sensitive information or actual work/project/runtime/raw was added to Git.
External artifacts exist: history receipt, verification project copy, scan/completion
reports and test logs; all stay outside the protocol repository.

Pre-qualification: dirty-tree full suite ran 442 tests in 715.579s and failed 10
memory setup checks (`execute a clean pinned code checkout`). Shared interpreter's
release gate returned base_release FAIL on the same dirty source. These runs are
not accepted evidence. Commit code before clean full qualification; append final
verification in a subsequent evidence commit. Do not weaken the pinned-code guard.

Final qualification on clean code `d751f28b6eaa99c38b155e3ff1537c1a1364b9f3`:

| Command (from repo root) | Exit | Result |
| --- | --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | 0 | 444 tests / 686.561s, OK |
| `<shared-venv>/bin/python -m unittest discover -s tests` | 0 | 444 tests / 682.755s, OK |
| `.venv/bin/python -m unittest discover -s tests -p test_works_history.py -v` | 0 | 10 tests, OK |
| Shared interpreter, same focused command | 0 | 10 tests, OK |
| Local/shared `tools/validate.py --check` | 0 | repository validation passed |
| `tools/security_check.py --check`, `tools/docs_check.py --check`, `git diff --check` | 0 | PASS |

Full-suite temporary roots were isolated and PYTHONDONTWRITEBYTECODE=1 was set.
System python3 lacks yaml, so the user-specified shared interpreter was used.
The two superseded dirty-tree suites and the dirty-tree release diagnostic were
stopped before clean qualification; they are not passing evidence. The preliminary
442-test run's 10 errors were pinned-code setup failures, resolved by the code commit.
Log hashes: local `f9e7bc46483a217b9f3cf8a585e13ba8ca2f452497591722e7bfc65b6e80c375`, shared
`0760e195d711d338a7f53f8433f0ab7dce2b808238bfd7d60411598c19308960`. No code changed after this qualification;
this follow-up updates completion evidence only. Real acceptance remains blocked
for the empty works index; publishing the requested PR does not claim a live review.
