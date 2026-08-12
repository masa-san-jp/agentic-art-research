# v1.2.0 release checklist

このチェックリストは、v1.2.0の公開対象を再現可能な検証済みmain commitへ固定するための記録である。チェック実行自体は公開・タグ作成・GitHub Releaseを行わない。

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
production-owned result schemaのsnapshotは登録済みであり、release判定では`--require-schema-snapshot`を付けて検査する。production schemaをresearch側で合成してはならない。
通常のGitHub Actions検証でもこのresearch-side gateを実行し、schema snapshotの欠落・改変はfail-closedで検査する。

production側のclean commit `fb15f32bf1eef0155c853c4b7c4b94df6b1bd78b`公開後、次でsnapshotとprovenanceを登録済みである。

```bash
python3 tools/snapshot_production_schema.py \
  --source-repo /path/to/agentic-art-production \
  --source-schema schemas/production-result.schema.json \
  --source-repository masa-san-jp/agentic-art-production \
  --commit <40-character-production-commit> \
  --acquired-at 2026-08-12T00:00:00+09:00 \
  --version 1.0.0
```

このCLIはdirty worktree、commit不一致、schema不正、path逸脱、既存snapshotの上書きを拒否する。

## 境界

このチェックリストは外部送信、配布、購入、契約を行わない。公開Releaseの作成は、このgateの成功後に人間承認を受けて実施し、v1.2.0では検証済みmain commit `a11d14b22323bdb7173839889b7b5a64754919f7`へ公開済みである。
