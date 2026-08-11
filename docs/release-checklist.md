# v1.0.0 release checklist

このチェックリストは、公開・タグ作成・GitHub Releaseを実行せず、リポジトリ内のMVP完了条件だけを確認する。

## 判定

- [x] 設計仕様書 §19.2 の10項目をチェック済み。
- [x] `tools/release_check.py` が必要パス、workflow、offline fixture、5ゲート、運用文書を確認する。
- [x] CI相当のcompile、validator、security、chaos、documentation、unit、graph、evaluationを3回連続で実行する。
- [x] `PRIVATE_RAW` と `RESTRICTED` を保存せず、個人由来の派生シグナルだけを利用できる。

## 実行

```bash
python3 tools/release_check.py --offline-fixture tests/fixtures/harmony
```

成功条件は、出力JSONの`passed`が`true`で、`ci_runs`が3件すべて成功すること。失敗時は
`mvp_items`、`spec_mvp`、`workflow`、または個別runの`output_tail`を確認する。

## 境界

このタスクでは、外部公開、タグ作成、GitHub Release、配布、購入、契約、送信は行わない。
それらは設計仕様書 §6.2 の人間承認対象である。
