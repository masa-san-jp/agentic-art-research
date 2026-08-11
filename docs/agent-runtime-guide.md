# Agent runtime guide

## 対象

ファイル編集、コマンド実行、Git差分確認が可能なGPT-5.6 LunaまたはClaude Sonnet級のコーディングエージェント。モデル識別子、認証、推論設定は実行環境側で設定し、リポジトリへ固定しない。

## 共通起動プロンプト

```text
このリポジトリを自律的に完成させてください。

AGENTS.mdを読み、docs/20260811-agentic-art-research-repository-execution-plan.mdを
ExecPlanとして実行してください。execution/task-queue.yamlで、依存関係がDONEの最小IDの
READYタスクを選び、受入条件を満たすまで実装・テスト・検証してください。

人間へ次工程を質問せず、完了後はキュー、state、ExecPlanのProgress・Discoveries・
Decision Log・Outcomesを更新して、次のREADYタスクへ進んでください。

設計仕様書§6.2に該当する場合だけ停止し、BLOCKED理由と解除条件を記録してください。
```

## Codex系

- `AGENTS.md`を常時規則として使う。
- 複数ファイルの変更はExecPlanに従う。
- 実行環境が長時間タスクを分割する場合も、`execution/state.yaml`を再開点にする。
- 推論設定は通常タスクで中程度、スキーマ変更・状態機械・安全境界では高めを選ぶ。モデル設定は環境側で行う。

## Claude Code系

- `CLAUDE.md`から共有規則へ入る。
- 独自のTodoだけを進捗の正本にせず、必ずrepo内のqueueとstateへ反映する。
- コンテキスト圧縮やセッション終了前に、正確な次操作を`resume_from`へ書く。

## 1セッションの上限

1セッションで複数タスクを進めてもよいが、次を満たすたびにcheckpointを残す。

- 1タスクの受入条件を満たした。
- schemaまたは公開CLIを変更した。
- 30分以上の試作結果が出た。
- 外部依存または設計との差を発見した。
- コンテキスト残量が少ない。

checkpointは、テスト可能な状態、queue更新、state更新、短い判断記録を含む。

## エスカレーション

強いモデルまたは人間へ上げる前に、次を実施する。

1. 失敗を最小fixtureで再現する。
2. 仕様、schema、実装のどこが競合するか特定する。
3. 既定再試行を使い切る。
4. 可逆な保守案と代替案を記録する。

設計仕様書§6.2に該当しなければ、保守案で進む。該当する場合はtaskを`BLOCKED`にし、質問ではなく、必要な決定、選択肢、影響、既定推奨をIssueへ記録する。

## 完了報告

実行系は次だけを報告する。

- 完了したtask IDと観察可能な動作
- 変更した正本と生成物
- 実行したテストと結果
- 残るgapまたはblocker
- 次のtask IDと最初の1操作

