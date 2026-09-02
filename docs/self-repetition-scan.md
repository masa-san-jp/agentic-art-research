# Self-repetition scan

`tools/self_repetition.py` is the deterministic, read-only pre-handoff check for repeated claims and hypotheses.
It reads one explicit candidate project and one explicit history root. The history root may be a locally
materialized export of completed Research projects or production plans. It must not contain private raw input
for this protocol operation.

The scanner reads only supported claim-bearing fields from these project-relative artifacts:

- `03_knowledge/claims.jsonl`
- `04_decisions/production-hypotheses.yaml`
- `05_production/production-brief.yaml`
- `05_production/creative-direction.md`
- `03_plan/production-plan.yaml`
- exported handoff creative-direction and hypothesis snapshots

Every invocation requires the repository name, the exact source commit for the supplied history snapshot, and a
fixed timestamp. The JSON report contains project-relative or opaque signal references, scores, and the measured
`LOW`/`MEDIUM`/`HIGH` level. It never copies claim text or absolute local paths to the report.

## Verify the four-project fixture

```bash
python3 tools/self_repetition.py \
  --candidate tests/fixtures/self-repetition/candidate \
  --history-root tests/fixtures/self-repetition/history \
  --repository masa-san-jp/agentic-art-research \
  --source-commit ce7e214f22e25c277c8f7277d83cd8cd85a3a8c1 \
  --now 2026-09-03T10:00:00+09:00 \
  --output /tmp/self-repetition-report.json \
  --check
```

The fixture must report four scanned projects, four matches, and `risk_level: HIGH`. To reflect the result into
the candidate's human-readable production direction, add `--project <candidate>` and `--apply`. Applying is
explicit, atomic, and idempotent; without `--apply` the candidate is never modified.
