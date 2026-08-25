# Governance

## 正本

| 対象 | 正本 |
|---|---|
| 要件・システム境界 | システム設計仕様書 |
| 実装順序・進捗 | 実行計画、task queue、state |
| 構造 | schemas |
| 語彙・閾値 | config |
| プロジェクト事実 | projects |
| 制作者派生シグナル | 外部profile output root（`--profiles-root`）。repositoryの`profiles/`はREADMEのみ |
| 索引・グラフ・監査 | data。生成物 |

## 設計変更

1. GitHub Issueへ問題、証拠、選択肢、影響を書く。
2. 後方互換性と移行方法を決める。
3. 設計、schema、config、code、testsを同じPRで更新する。
4. Decision Logへ理由を残す。

## 安全・権利・個人情報

- 私的原データをGitへ置かない。
- 認証情報をファイル、ログ、Issue、fixtureへ置かない。
- 公開・応募・外部送信は人間承認なしに実行しない。
- 第三者情報は目的に必要な最小限へ派生・匿名化する。
- 権利不明資料はURLの記録に留め、snapshotを保存しない。
- 削除は依存レポートと人間承認を必要とする。

## エージェントの停止

エージェントは失敗を無限に反復しない。既定再試行を使い切ったら、`BLOCKED` または `COMPLETE_WITH_GAPS` とし、原因、試行、解除条件を記録する。
