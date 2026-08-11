# Self Model Notes consumer contract

The research system consumes the derived `research_signals` boundary from
[`masa-san-jp/self-model-notes`](https://github.com/masa-san-jp/self-model-notes).
It does not copy the upstream knowledge-base entities into this repository.

## Pinned contract

- Schema: `urn:self-model-notes:research-signals:v1`
- Upstream commit: `self-model-notes@7f1f371486fe983f0bcfefbbf92a5df1326dac7b`
- Upstream schema: [`research-signals-v1.schema.json`](https://github.com/masa-san-jp/self-model-notes/blob/7f1f371486fe983f0bcfefbbf92a5df1326dac7b/tests/contracts/research-signals-v1.schema.json)
- Offline fixture: [`tests/contracts/self-model-notes-research-signals-v1.json`](../tests/contracts/self-model-notes-research-signals-v1.json)

The fixture is intentionally synthetic and contains only the contract boundary.
When the upstream knowledge base does not provide a signal, the consumer keeps
the result explicit as `certainty: unknown` with empty signal and evidence
references. It must not infer a preference or copy raw voice data.

Run the consumer contract test with:

```bash
python3 -m unittest tests.test_research_signals_v1_contract -v
```
