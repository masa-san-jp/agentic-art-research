# Offline evaluation gates

`tools/evaluate.py` runs the MVP quality and safety gates against the
synthetic `harmony` project without network access or private source access.
It materializes the fixture, then emits the machine-readable contract
`urn:agentic-art-research:evaluation:v1`.

The report checks:

- `accuracy`: the fixture oracle status and expected evidence-to-production
  trace IDs;
- `traceability`: the expected upstream question and downstream chain;
- `termination`: terminal project state, replay consistency, and mandatory
  question termination;
- `resume`: expired lease recovery and duplicate-effect suppression;
- `privacy`: repository validation, no private-raw completion flag, and the
  private evidence adapter's accept/reject boundary; and
- `audit`: no structural or quality findings at the fixed evaluation time.

Run it in a clean evaluation workspace:

```bash
python3 tools/evaluate.py \
  --offline-fixture tests/fixtures/harmony \
  --root /tmp/agentic-art-evaluation \
  --output /tmp/agentic-art-evaluation-report.json
```

The fixture is synthetic. The evaluator never resolves opaque private URIs,
and its privacy probe uses metadata placeholders only.
