# Private evidence adapter

`tools/private_evidence_adapter.py` is the repository boundary for a
consent-approved private source. It does not resolve or read the private
source. The caller supplies only a descriptor and an already-derived signal.

The accepted input contains:

- an opaque `source_location` such as `gdrive://opaque-evidence-901`;
- a lowercase `sha256:` content hash and acquisition timestamp;
- rights, sensitivity, related project/question IDs, and an opaque approval
  reference;
- a pinned signal source (`schema`, repository, and commit); and
- controlled derived signals: category, short code, certainty, and evidence
  IDs.

The output is schema `urn:agentic-art-research:private-evidence-adapter:v1`.
It contains the evidence metadata, approval metadata, pinned signal source,
and `approved_derived_signals`. It never contains source text, body content,
transcripts, audio, attachments, canonical entities, or claims.

`PRIVATE_RAW` and `RESTRICTED` are rejected. The adapter also rejects unknown
fields, non-opaque URLs, malformed hashes, missing approval provenance, and
the `raw_voice_refs` signal category. The adapter requires `PRIVATE_DERIVED`
as input; it does not silently downgrade raw material into an exportable
classification.

Use a synthetic descriptor in offline tests:

```bash
python3 tools/private_evidence_adapter.py \
  --input /path/to/private-derived-descriptor.json \
  --output /tmp/private-derived-output.json
```

The adapter is a normalization and validation boundary only. Consent and any
human approval required to derive or reclassify private information must be
performed by the upstream connector and represented by the opaque approval
reference; no credentials or private source contents belong in this
repository.
