# v1.0.0 release checklist

Run the release gate from the repository root:

```bash
python3 tools/release_check.py \
  --offline-fixture tests/fixtures/harmony \
  --ci-evidence execution/ci-evidence.json \
  --output /tmp/agentic-art-release-check.json
```

The check is repository-local and offline. It verifies the directory and
schema contracts, project generation and validation, graph/bundle/impact
outputs, the terminal sample fixture, all E2E quality and safety gates, the
MVP checklist in the design specification, and three successful `validate`
Actions runs.

The recorded CI evidence is public run metadata for PRs #20, #21, and #22.
It does not contain credentials, private source data, or source snapshots.

Creating a GitHub Release or announcing a release is intentionally separate
from this local gate because it is an external publication action requiring
human approval.
