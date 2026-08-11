# v1.0.1 release checklist

このチェックリストは、公開・タグ作成・GitHub Releaseを実行せず、リポジトリ内のMVP完了条件だけを確認する。

## 判定

- [x] 設計仕様書 §19.2 の10項目をチェック済み。
- [x] `tools/release_check.py` が必要パス、offline fixture、5ゲート、運用文書、追加hardeningを確認する。
- [x] CI evidenceのvalidate成功3件に加え、security、chaos、documentationを検査する。
- [x] `PRIVATE_RAW` と `RESTRICTED` を保存せず、個人由来の派生シグナルだけを利用できる。

## 実行

```bash
python3 tools/release_check.py \
  --offline-fixture tests/fixtures/harmony \
  --ci-evidence execution/ci-evidence.json
```

成功条件は、出力JSONの`passed`が`true`で、`checks`内の11項目がすべて成功すること。
失敗時は各checkの`details`を確認する。

制作引き渡し拡張のresearch-side gateは次で確認する。

```bash
python3 tools/handoff_release_check.py \
  --offline-fixture tests/fixtures/harmony \
  --handoff-fixture tests/fixtures/harmony-handoff \
  --ci-evidence execution/ci-evidence.json
```

このgateの`passed: true`はresearch側のbundle、fail-closed、後方互換性を示す。
`schema_snapshot_ready: false`はproduction-owned result schema未公開を明示する状態であり、
`--require-schema-snapshot`を付けた場合だけ失敗として扱う。production schemaをresearch側で合成してはならない。

## 境界

このタスクでは、外部公開、タグ作成、GitHub Release、配布、購入、契約、送信は行わない。
それらは設計仕様書 §6.2 の人間承認対象である。
