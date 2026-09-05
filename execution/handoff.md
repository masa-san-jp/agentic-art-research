# AAK-08 verified handoff

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
