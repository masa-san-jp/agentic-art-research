# Research element contract provenance

Issue #122 snapshots `schemas/element-request.schema.json`, `element-answer.schema.json`,
`search-request.schema.json`, and `search-answer.schema.json` verbatim from
`masa-san-jp/agentic-art-orchestration` commit
`337c33211d6048d934b2db0dcf749cd25cb2e617` (2026-10-08).

`tools/research_element_checks.py` is the mechanical check module from the same commit,
renamed to avoid confusing the research CLI with the parent's CLI. The research
scheduler and persistence engine are repo-owned. They follow chapters 2, 3 and 5 of
`docs/20261007-element-harness-design.md` in that source commit. Contract updates must
be explicit; the runtime never imports a sibling checkout.
