# Handoffs

長時間タスクを途中で止める場合だけ、`<task-id>-<timestamp>.md` を作る。

必須項目:

- task IDと現在状態
- 変更済みファイル
- 実行済みコマンドと結果
- 失敗の再現手順
- 重要な判断と未解決事項
- 次に実行する正確な1操作

通常の完了タスクは、タスクキュー、state、ExecPlan、commitで十分なのでhandoffを作らない。

