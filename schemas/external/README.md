# External schema snapshots

外部リポジトリが正本とするschemaは、生成側で公開された後にだけ、このdirectoryへimmutable snapshotとして追加する。

各snapshotは次を伴う。

- source repository
- source commitの完全な40桁SHA
- 取得日時（timezone付きRFC 3339）
- snapshot fileのSHA-256
- 対応するcontract version
- 更新元を検証する再取得手順

`production-result.schema.json`の正本は`masa-san-jp/agentic-art-production`が所有する。2026-08-11時点ではproduction repoが設計段階で正本schemaをまだ公開していないため、consumer側で仮schemaや偽のsnapshotを作らない。公開後、`FEEDBACK-IMPORT-001`で`production-result.v1.schema.json`というcommit固定snapshotを取得し、互換性fixtureを追加する。未公開または取得不能な間、production result importは実装済みと扱わずfail closedする。
