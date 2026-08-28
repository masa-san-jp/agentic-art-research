# Agent runtime guide

最短の利用開始手順は [README](../README.md) を参照する。この文書は、projectを実行するagentとprovider adapterが守るruntime契約の詳細である。
このrepositoryのAIはリサーチと検証を扱い、作品を制作しない。protocol rootは読み取り専用、project/runtimeはwork root、検証済み出力はprotocol repository外のoutput rootに分離する。

## 対象

ファイル編集、コマンド実行、Git差分確認が可能なGPT-5.6 LunaまたはClaude Sonnet級のコーディングエージェント。モデル識別子、認証、推論設定は実行環境側で設定し、リポジトリへ固定しない。

## タスクDAGの実行と再開

`RUNTIME-002` のタスク定義はプロジェクトの `01_planning/research-plan.yaml` に置く。
各タスクは `id`、`depends_on`、任意の `max_attempts` を持ち、初期化後のsnapshotは
`07_runtime/research-state.json.task_runtime`、claim・lease期限・retry・完了は
`07_runtime/run-log.jsonl`へ記録する。

```bash
PYTHON=".venv/bin/python"
PROTOCOL_ROOT="$(pwd)"
WORK_ROOT="/path/to/work-root"
PROJECT_ID="project/example"

"$PYTHON" "$PROTOCOL_ROOT/tools/task_runtime.py" "$PROJECT_ID" init \
  --now 2026-08-11T00:00:00+09:00 --root "$WORK_ROOT"
"$PYTHON" "$PROTOCOL_ROOT/tools/task_runtime.py" "$PROJECT_ID" claim \
  --worker-id worker-a --root "$WORK_ROOT"
"$PYTHON" "$PROTOCOL_ROOT/tools/task_runtime.py" "$PROJECT_ID" resume \
  --now 2026-08-11T00:10:00+09:00 --root "$WORK_ROOT"
```

作業開始前に次のtaskを確認するだけなら、`next_action.py --dry-run`を使う。これはleaseをclaimせず、`research-state.json`や`run-log.jsonl`も変更しない。agentはtaskを自分で選ばず、この入口が返すtask・context・write targets・acceptanceを使う。

```bash
PYTHON=".venv/bin/python"
PROTOCOL_ROOT="$(pwd)"
WORK_ROOT="/path/to/work-root"
OUTPUT_ROOT="/path/to/output-root"
PROJECT_ID="project/example"

"$PYTHON" tools/next_action.py "$PROJECT_ID" \
  --worker worker-a \
  --now 2026-08-25T00:00:00+09:00 \
  --dry-run \
  --protocol-root "$PROTOCOL_ROOT" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT"
```

未claim taskは`TASK_PREVIEWED`（`lease: null`）、同じworkerが保持中のtaskは`TASK_RESUME_PREVIEW`、予算超過は`BUDGET_EXCEEDED`、ready taskなしは`NO_TASK_READY`になる。previewと同じworker・時刻でliveを実行した場合、task ID、role、context、write targets、acceptanceは一致する。

### Worker attempt adapter

taskを実行するproviderはrepositoryへ固定しない。supervisorは、claim済みtaskから`schemas/agent-attempt-request.schema.json`に適合するrequestを作り、次のadapter入口へ渡す。

```bash
ATTEMPT_REQUEST="$WORK_ROOT/.harness/attempts/HR001/TASK001/AT001/request.json"

"$PYTHON" tools/worker_adapter.py run \
  --request "$ATTEMPT_REQUEST" \
  --adapter fake \
  --protocol-root "$PROTOCOL_ROOT"
```

adapter設定は`config/worker-adapters.yaml`のargv、capability、environment allowlist、timeout、stdout/stderr上限だけを読み取る。commandはshell文字列ではなくargv配列であり、shell interpreterとmetacharacterを拒否する。workerのstdoutは一つのresult JSON、stderrはbounded diagnosticとし、`agent-attempt-result.schema.json`に適合しない出力、timeout、exit/signal、output limit、secret、未許可capabilityは名前付き`WORKER-*` failure resultになる。

このadapterはattempt resultを返すだけで、`07_runtime/research-state.json`、`run-log.jsonl`、task lease、acceptance、output adoptionを変更しない。`HUMAN_REQUIRED`はtyped decision requestとして後続のdecision flowへ渡し、taskのcomplete/promotionは別の原子操作で行う。認証情報はschema、ログ、manifestへ入れず、allowlistされた環境変数の値もdiagnosticからredactする。

### Attempt file boundary

worker processのcwdは`<work-root>/.harness/attempts/<run>/<task>/<attempt>/project`であり、canonical projectではない。`tools/attempt_workspace.py`がbaseline/protected manifestを作り、worker終了後にbefore/afterを`attempt-changeset.schema.json`へ変換する。`config/task-roles.yaml#roles.<role>.write_targets`のexact relative pathだけをworker file setとし、`runtime_targets`はharnessの別namespaceとしてadapter/promotionから隔離する。

`inspect_attempt`はprotocol root、canonical project以外のwork root、別project、data、output rootの変更も検出する。許可外path、schema/private/secret/size違反、symlink・hard-link・特殊file・path traversal・case/Unicode collisionがある場合、許可内変更を含めて全attemptを拒否する。`promote_attempt`はproject lock下でbaseline hashを再確認し、candidate treeを作ってから交換するため、同時変更は`ATTEMPT-BASELINE-CONFLICT`となり既存projectを上書きしない。

### Typed acceptance and task completion

`next_action.py`が返す`acceptance`は、`id`・`kind`・project-relativeな判定対象と、明示的な
`protocol_root`・`work_root`・`output_root`を持つtyped gateである。任意shell文字列、cwd、PATH、
workerが指定する完了コマンドは受け付けない。roleの正本は`config/task-roles.yaml#acceptance_checks`、
gateの語彙とレポートは`schemas/acceptance-gate.schema.json`と
`schemas/acceptance-report.schema.json`にある。

workerがattempt projectを編集した後は、次のハーネスAPIだけが受入判定、changeset promotion、
blocking validation、task runtimeの`SUCCEEDED`を順に実行する。

```python
from acceptance_executor import complete_attempt

complete_attempt(
    attempt,
    protocol_root=protocol_root,
    work_root=work_root,
    output_root=output_root,
    worker_id=worker_id,
    lease_token=lease_token,
    evaluated_at="2026-08-25T00:00:00+09:00",
)
```

gateが一つでも失敗した場合は`ACCEPTANCE-GATE-FAILED`のレポートを残し、attemptの変更は
promotionせず、taskは`VALIDATION`として再試行可能になる。promotion後のvalidationまたは
completeが失敗した場合は、attempt内のtransaction journalとpreimageからcanonical project、
runtime state、run-logを復元する。成功済みattemptの同じreport/changesetの再送は同じ結果を返し、
異なるeffectは`ACCEPTANCE-PROMOTION-CONFLICT`で拒否する。

`tools/task_runtime.py ... complete`の直接CLI呼び出しは、typed acceptanceを経ない限り
`TASK-COMPLETE-WITHOUT-GATE`で拒否される。workerはruntime state/logを直接書かず、roleの
`runtime_targets`はharness namespaceとして管理する。

### Immutable archive provenance

親の品質ゲートが子リポジトリを`git archive <observed-commit>`から実行する場合、展開先には`.git`がない。リポジトリルートの`.archive-commit`は`.gitattributes`の`export-subst`でarchive作成元のcommit SHAへ置換され、`harness.py`はGit metadataがない場合だけこのmarkerを読む。markerがない、symlinkである、または40桁の小文字SHAでないarchiveは`HARNESS-PROTOCOL-PROVENANCE`で拒否する。

通常のGit checkoutでは従来どおり`git rev-parse HEAD`、working tree、index、untracked fileを検証する。archiveでは親runnerがmanifestの`observed_commit`からarchiveを作成し、markerの値とpinを照合する。`PYTHONDONTWRITEBYTECODE=1`やwarm cacheでこの経路を隠してはならず、cold archiveで宣言済みquality gateを実行する。

reference matrixは各scenarioを独立したcold subprocessで実行し、`kill-each-phase`の各phaseも同じ方式で並列化する。結果はfixtureの順序で回収するためreportのbytesとscenario順は決定的であり、Supervisorのsignal handlerやプロセス間のmutable cacheを共有しない。

### Human decision request / resolve

設計仕様§6.2の6分類に該当する場合だけ、adapterの`HUMAN_REQUIRED` resultを
`harness.record_attempt_result`へ渡す。requestは`07_runtime/human-decisions.yaml`へ保存され、
taskは`WAITING_HUMAN`へ移る。leaseは解放され、attempt回数は消費せず、依存taskはBLOCKEDにしない。

```bash
RESPONSE="/path/to/human-decision-response.json"
"$PYTHON" tools/harness.py decisions list "$PROJECT_ID" \
  --protocol-root "$PROTOCOL_ROOT" --work-root "$WORK_ROOT"
"$PYTHON" tools/harness.py decisions resolve "$PROJECT_ID" \
  --protocol-root "$PROTOCOL_ROOT" --work-root "$WORK_ROOT" \
  --response "$RESPONSE"
```

request/responseは`human-decision-request.schema.json`と
`human-decision-response.schema.json`に適合し、canonical hash、run/project/task/attempt、actor、
理由、選択肢を固定する。stale hash、別identity、unknown option、二重解決、未承認categoryは
`HUMAN-DECISION-STALE`、`HUMAN-DECISION-STATE`、`HUMAN-DECISION-OPTION`、
`HUMAN-DECISION-REPLAY`、`HUMAN-DECISION-CATEGORY`として拒否する。
resolve後はtaskが新しいleaseを取得できる`PENDING`へ戻り、次のcontext packにresponse ID/hashと
actionだけを含める。承認に書かれていない制作判断は推測せず、通知や外部送信は行わない。

### Project supervisor

個別のclaim、worker、heartbeat、acceptance、completeを手で連結せず、1 runをbounded loopで運転する場合はsupervisorを使う。既にbootstrap済みのprojectを対象にする互換入口であり、新規requestは次の「public CLI」の`--request`形式を使う。

```bash
"$PYTHON" tools/harness.py run "$PROJECT_ID" \
  --protocol-root "$PROTOCOL_ROOT" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001 --worker supervisor-a --adapter fake

"$PYTHON" tools/harness.py resume "$PROJECT_ID" \
  --protocol-root "$PROTOCOL_ROOT" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001 --worker supervisor-a --adapter fake
```

supervisorは`reconcile → claim → attempt workspace → worker → changeset → acceptance/promotion/complete`の順序を固定し、`<work-root>/.harness/supervisor/<project>/<run>.json`へhashと状態だけを記録する。lease token、project本文、credential、private marker、絶対ローカルパスはjournalへ保存しない。同じproject/runの同時実行は`HARNESS-RUN-LOCKED`で拒否される。

worker実行中は設定された間隔でheartbeatを送り、失敗時はworkerを停止して`HARNESS-HEARTBEAT`とretry/terminal判定へ接続する。process crash後はjournal、attempt result、acceptance transaction、期限切れleaseを照合し、完了済みresultを再実行せずにresumeする。SIGINT/SIGTERMは新規claimを止め、`HARNESS-SHUTDOWN`と実行可能なresume commandを残す。`HUMAN_REQUIRED`は正常な`PAUSED` outcomeとしてdecision requestを返し、resolve後に同じtaskを再開する。max runtime、max tasks、retry上限のいずれかで必ず停止する。

queue/stateの実行順序はrepository rootの`execution/task-queue.yaml`と`execution/state.yaml`が正本である。次のtaskを手で推測せず、次で矛盾を検査する。

```bash
"$PYTHON" tools/validate.py --check
```

validatorは未知dependency、循環、依存未完了taskのREADY化、複数IN_PROGRESS、誤った`next_task`、全task完了前の`terminal: true`をblockingにする。

workerが停止した場合は期限切れleaseだけが再取得対象になる。失敗は設定済みの
`TRANSIENT`、`TIMEOUT`、`RATE_LIMIT`などの分類を必須とし、最大試行回数を超えて
同じ失敗を反復しない。完了効果にはタスク単位の決定的な`effect_key`を使い、同じ
効果の再送は既存結果を返し、異なる効果キーの二重適用は拒否する。

### Request から verified handoff までの public CLI

request受理からoutput publishまでを一つの入口で実行する場合は、requestと3つのrootを
明示する。`--now`を渡すと全phaseとworker deadlineに同じRFC 3339 clockが使われる。
`fake` はreference fixture用で、任意のrequestを完了させるworkerではない。

```bash
REQUEST="./research-request.yaml"
ADAPTER="your-configured-adapter"
WORK_ROOT="$(mktemp -d /tmp/agentic-art-work.XXXXXX)"
OUTPUT_ROOT="$(mktemp -d /tmp/agentic-art-output.XXXXXX)"

"$PYTHON" tools/harness.py run \
  --request "$REQUEST" \
  --adapter "$ADAPTER" \
  --protocol-root "$PROTOCOL_ROOT" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001 \
  --now 2026-08-25T00:00:00+09:00
```

成功時は`<output-root>/<project-slug>/`へ`research-project/`、`handoff/`,
`run-manifest.json`、`checksums.json`だけがatomicに配置される。stdoutは
`schemas/harness-outcome.schema.json`に適合する一つのJSONだけであり、公開manifestの
`outcome_sha256`と一致する。canonical protocol root、別project、production repository
へは書き込まない。

`PAUSED`、worker failure、completion/handoff failureではoutputを作らず、次の形式で再開する。

```bash
"$PYTHON" tools/harness.py resume \
  --protocol-root "$PROTOCOL_ROOT" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001
```

resumeはwork journalに保存したadapter設定とrequest identityを再利用する。同じ
request・worker・protocol・runの既存outputは`ALREADY_PUBLISHED`になり、異なるfingerprintや
一部だけ残ったoutputは`HARNESS-PUBLISH-CONFLICT`で上書きしない。phase、task counts、
completion、handoff ID、artifact hashesはoutcomeとmanifestで照合できる。

### Provider conformance

任意providerの実workerは、公開前に同じattempt request/result契約をofflineで確認できる。
requestのworkspaceだけを使い、credentialやprompt本文を保存せず、結果はadapter ID・status・failure class・result hashだけを返す。

```bash
ATTEMPT_REQUEST="$WORK_ROOT/.harness/attempts/HR001/TASK001/AT001/request.json"

"$PYTHON" tools/harness.py conformance \
  --request "$ATTEMPT_REQUEST" \
  --adapter fake \
  --protocol-root "$PROTOCOL_ROOT" \
  --worker-command '["python3", "path/to/provider-worker.py"]'
```

CIのblocking workerはreference fake workerであり、provider conformanceは同じschema境界を満たすことだけを確認する。料金、model品質、外部telemetryは評価しない。

## 停止規則と飽和

質問ごとの `SEARCH_ATTEMPTED`、`SOURCE_REVIEWED`、`ANSWER_FOUND`、
`EVIDENCE_ROUND`、`SEARCH_FAILED` を `run-log.jsonl`へ記録し、次で評価・適用する。

```bash
"$PYTHON" tools/stopping_policy.py "$PROJECT_ID" evaluate
"$PYTHON" tools/stopping_policy.py "$PROJECT_ID" apply --evaluated-at 2026-08-11T00:20:00+09:00
```

`ANSWER_FOUND` が十分数に達した場合は `ANSWERED` を優先する。それ以外は検索戦略、
確認資料、同一失敗の反復、連続した新規証拠なしラウンドのいずれかが上限に達した時点で
`UNRESOLVED` または `BLOCKED` に終端化する。applyは同じ入力へ再実行しても停止イベントを
重複追記しない。

## ロール別context pack

workerへはプロジェクト全体を渡さず、task、role固有の宣言済みsource、制約、受入試験だけを
含む最小packを生成する。

```bash
"$PYTHON" tools/context_pack.py "$PROJECT_ID" TASK001 --role planner
"$PYTHON" tools/context_pack.py "$PROJECT_ID" TASK002 --role production-translator -o /tmp/context.json
```

未知のrole、存在しないtask、欠損source、プロジェクト外へ解決されるsourceは失敗する。
packは生成物であり、正本の代わりに編集してはならない。

## 品質・安全性評価

offline fixtureはcanonical rootを書き換えず、一時領域で正確性、追跡性、終端性、再開性、安全性を評価する。

```bash
"$PYTHON" tools/evaluate.py --offline-fixture tests/fixtures/harmony
"$PYTHON" tools/security_check.py --check
"$PYTHON" tools/chaos_check.py
"$PYTHON" tools/docs_check.py --check
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
