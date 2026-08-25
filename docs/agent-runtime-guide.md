# Agent runtime guide

## 対象

ファイル編集、コマンド実行、Git差分確認が可能なGPT-5.6 LunaまたはClaude Sonnet級のコーディングエージェント。モデル識別子、認証、推論設定は実行環境側で設定し、リポジトリへ固定しない。

## タスクDAGの実行と再開

`RUNTIME-002` のタスク定義はプロジェクトの `01_planning/research-plan.yaml` に置く。
各タスクは `id`、`depends_on`、任意の `max_attempts` を持ち、初期化後のsnapshotは
`07_runtime/research-state.json.task_runtime`、claim・lease期限・retry・完了は
`07_runtime/run-log.jsonl`へ記録する。

```bash
python3 tools/task_runtime.py project/example init --now 2026-08-11T00:00:00+09:00
python3 tools/task_runtime.py project/example claim --worker-id worker-a
python3 tools/task_runtime.py project/example resume --now 2026-08-11T00:10:00+09:00
```

作業開始前に次のtaskを確認するだけなら、`next_action.py --dry-run`を使う。これはleaseをclaimせず、`research-state.json`や`run-log.jsonl`も変更しない。

```bash
python3 tools/next_action.py project/example \
  --worker worker-a \
  --now 2026-08-25T00:00:00+09:00 \
  --dry-run \
  --root <temporary-root>
```

未claim taskは`TASK_PREVIEWED`（`lease: null`）、同じworkerが保持中のtaskは`TASK_RESUME_PREVIEW`、予算超過は`BUDGET_EXCEEDED`、ready taskなしは`NO_TASK_READY`になる。previewと同じworker・時刻でliveを実行した場合、task ID、role、context、write targets、acceptanceは一致する。

### Worker attempt adapter

taskを実行するproviderはrepositoryへ固定しない。supervisorは、claim済みtaskから`schemas/agent-attempt-request.schema.json`に適合するrequestを作り、次のadapter入口へ渡す。

```bash
python3 tools/worker_adapter.py run \
  --request <attempt-request.json> \
  --adapter fake \
  --protocol-root <protocol-root>
```

adapter設定は`config/worker-adapters.yaml`のargv、capability、environment allowlist、timeout、stdout/stderr上限だけを読み取る。commandはshell文字列ではなくargv配列であり、shell interpreterとmetacharacterを拒否する。workerのstdoutは一つのresult JSON、stderrはbounded diagnosticとし、`agent-attempt-result.schema.json`に適合しない出力、timeout、exit/signal、output limit、secret、未許可capabilityは名前付き`WORKER-*` failure resultになる。

このadapterはattempt resultを返すだけで、`07_runtime/research-state.json`、`run-log.jsonl`、task lease、acceptance、output adoptionを変更しない。`HUMAN_REQUIRED`はtyped decision requestとして後続のdecision flowへ渡し、taskのcomplete/promotionは別の原子操作で行う。認証情報はschema、ログ、manifestへ入れず、allowlistされた環境変数の値もdiagnosticからredactする。

### Attempt file boundary

worker processのcwdは`<work-root>/.harness/attempts/<run>/<task>/<attempt>/project`であり、canonical projectではない。`tools/attempt_workspace.py`がbaseline/protected manifestを作り、worker終了後にbefore/afterを`attempt-changeset.schema.json`へ変換する。`config/task-roles.yaml#roles.<role>.write_targets`のexact relative pathだけをworker file setとし、`runtime_targets`はharnessの別namespaceとしてadapter/promotionから隔離する。

`inspect_attempt`はprotocol root、canonical project以外のwork root、別project、data、output rootの変更も検出する。許可外path、schema/private/secret/size違反、symlink・hard-link・特殊file・path traversal・case/Unicode collisionがある場合、許可内変更を含めて全attemptを拒否する。`promote_attempt`はproject lock下でbaseline hashを再確認し、candidate treeを作ってから交換するため、同時変更は`ATTEMPT-BASELINE-CONFLICT`となり既存projectを上書きしない。

queue/stateの実行順序はrepository rootの`execution/task-queue.yaml`と`execution/state.yaml`が正本である。次のtaskを手で推測せず、次で矛盾を検査する。

```bash
python3 tools/validate.py --check
```

validatorは未知dependency、循環、依存未完了taskのREADY化、複数IN_PROGRESS、誤った`next_task`、全task完了前の`terminal: true`をblockingにする。

workerが停止した場合は期限切れleaseだけが再取得対象になる。失敗は設定済みの
`TRANSIENT`、`TIMEOUT`、`RATE_LIMIT`などの分類を必須とし、最大試行回数を超えて
同じ失敗を反復しない。完了効果にはタスク単位の決定的な`effect_key`を使い、同じ
効果の再送は既存結果を返し、異なる効果キーの二重適用は拒否する。

## 停止規則と飽和

質問ごとの `SEARCH_ATTEMPTED`、`SOURCE_REVIEWED`、`ANSWER_FOUND`、
`EVIDENCE_ROUND`、`SEARCH_FAILED` を `run-log.jsonl`へ記録し、次で評価・適用する。

```bash
python3 tools/stopping_policy.py project/example evaluate
python3 tools/stopping_policy.py project/example apply --evaluated-at 2026-08-11T00:20:00+09:00
```

`ANSWER_FOUND` が十分数に達した場合は `ANSWERED` を優先する。それ以外は検索戦略、
確認資料、同一失敗の反復、連続した新規証拠なしラウンドのいずれかが上限に達した時点で
`UNRESOLVED` または `BLOCKED` に終端化する。applyは同じ入力へ再実行しても停止イベントを
重複追記しない。

## ロール別context pack

workerへはプロジェクト全体を渡さず、task、role固有の宣言済みsource、制約、受入試験だけを
含む最小packを生成する。

```bash
python3 tools/context_pack.py project/example TASK001 --role planner
python3 tools/context_pack.py project/example TASK002 --role production-translator -o /tmp/context.json
```

未知のrole、存在しないtask、欠損source、プロジェクト外へ解決されるsourceは失敗する。
packは生成物であり、正本の代わりに編集してはならない。

## 品質・安全性評価

offline fixtureはcanonical rootを書き換えず、一時領域で正確性、追跡性、終端性、再開性、安全性を評価する。

```bash
python3 tools/evaluate.py --offline-fixture tests/fixtures/harmony
python3 tools/security_check.py --check
python3 tools/chaos_check.py
python3 tools/docs_check.py --check
```

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
