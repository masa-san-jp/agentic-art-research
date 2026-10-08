# Research element contract provenance

Issue #122 snapshots `schemas/element-request.schema.json`, `element-answer.schema.json`,
`search-request.schema.json`, and `search-answer.schema.json` verbatim from
`masa-san-jp/agentic-art-orchestration` commit
`337c33211d6048d934b2db0dcf749cd25cb2e617` (2026-10-08).

`tools/research_element_checks.py` originates from the mechanical check module at the
same commit, renamed to avoid confusing the research CLI with the parent's CLI.
Issue #122's review adds the local `source_terms_or_sentence` check for grounded
excerpts and an input-supplied threshold for `not_similar_to` (the original default
remains 0.8; observation/claim derivation uses 0.9). The wire schemas remain verbatim.
The research
scheduler and persistence engine are repo-owned. They follow chapters 2, 3 and 5 of
`docs/20261007-element-harness-design.md` in that source commit. Contract updates must
be explicit; the runtime never imports a sibling checkout.
