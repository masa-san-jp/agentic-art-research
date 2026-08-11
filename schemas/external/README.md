# External schema snapshots

外部リポジトリが正本とするschemaは、生成側で公開された後にだけ、このdirectoryへimmutable snapshotとして追加する。

各snapshotは次を伴う。

- source repository
- source commitの完全な40桁SHA
- 取得日時（timezone付きRFC 3339）
- snapshot fileのSHA-256
- 対応するcontract version
- 更新元を検証する再取得手順

research側のimporterは、schema本体を`config/handoff-policy.yaml`の
`production_result_schema_path`から読み、同じpolicyの
`production_result_schema_source`でsnapshot provenanceを検証する。provenanceは次のfieldを持つ。

```yaml
production_result_schema_source:
  repository: masa-san-jp/agentic-art-production
  commit: "<40-character-lowercase-git-sha>"
  acquired_at: "2026-08-11T22:00:00+09:00"
  sha256: "sha256:<64-lowercase-hex>"
```

schema本体、source provenance、対応versionのいずれかがない間、
`import_production_result.py`はdry-runを含めてfail-closedする。research側でproduction
result schemaを仮定義してはならない。

`production-result.schema.json`の正本は`masa-san-jp/agentic-art-production`が所有する。Production clean commit `fb15f32bf1eef0155c853c4b7c4b94df6b1bd78b`から`production-result.v1.schema.json`を取得済みであり、raw SHA-256は`sha256:5b69090476891629932e5b01260a6217273f8a4771cc004d4922fcd04a0a104a`である。snapshotのsource provenanceを改変・欠落させた場合、production result importはdry-runを含めてfail closedする。
