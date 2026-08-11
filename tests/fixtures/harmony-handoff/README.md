# Harmony cross-repository handoff fixture

このfixtureは、`tests/fixtures/harmony`を入力として、テスト時にhandoff bundleと
production resultを生成するための境界説明である。bundleとresultの正本bytesは
テストごとの一時rootに生成し、リポジトリへ実projectやraw assetを保存しない。

production result schemaは`agentic-art-production`が所有する。production側のclean
commitが公開されるまで、schema snapshotをこのfixtureへ仮置きせず、互換性テストは
test-only schemaを一時rootへ注入して行う。実運用importは必ず
`config/handoff-policy.yaml`のproduction-owned snapshot provenanceを要求する。
