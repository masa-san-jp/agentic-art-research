# Execution Plans

ExecPlanは、長時間または複数ファイルにまたがる変更を、別セッションの実行系エージェントが引き継いで完了できる自己完結型の計画である。

## 使用条件

次のいずれかならExecPlanを使う。

- 3ファイル以上を変更する。
- 新しいスキーマ、状態、CLI、データ移行を追加する。
- 2時間以上または複数セッションになる可能性がある。
- 設計上の選択、試作、外部依存の確認が必要である。

## 必須原則

- 読み手に過去の会話、隠れた記憶、口頭説明がない前提で書く。
- 目的、利用者が確認できる動作、変更箇所、実行コマンド、期待結果を含める。
- 計画は生きた文書であり、実行中に更新する。
- 曖昧さをユーザーへ戻さず、仕様と保守的な既定値で解決する。
- 各マイルストーンは、単なるファイル作成ではなく観察可能な動作で完了を判定する。
- 途中停止しても、計画とリポジトリだけから再開できる。

## 必須セクション

### Purpose / Big Picture

何が可能になり、どのコマンドまたは成果物で確認できるか。

### Progress

- [x] `HANDOFF-REFERENCE-CONTRACT-001`: Issue #43のsource-ref Producer/Consumer契約をResearch側から公開する。Production consumerの実装・mergeは別repoの責務として行わない。120 testとvalidatorが合格。
- [x] (2026-08-16 JST) `COMPLETION-QUALITY-001`: Issue #44の調査量・先行作品調査・自己反復リスクを完了条件へ組み込む。未達は`INCOMPLETE`としてhandoffを拒否する。123 unittest、validator、docs、security、chaos、graph、offline evaluation、release、handoff release gates合格。
- [x] (2026-08-25 JST) `RUNTIME-005`: `task_runtime.peek_next()`、`next_action.py --dry-run`、preview/live一致テスト、execution queue/stateのblocking validatorを実装。対象58テスト合格。次は`BOUNDARY-001`。
- [x] (2026-08-25 JST) `BOUNDARY-001`: canonical repositoryから実プロジェクトを隔離し、空のgraph、`PROTOCOL-OUTPUT-BOUNDARY`検査、明示`--root` materialization CLI、CI/docs契約を追加。次は`DECISION-001`。
- [x] (2026-08-25 JST) `DECISION-001`: `RO###`／`U###` typed registry、判断との双方向参照、決定的executive brief、graph/bundle接続を実装。177 unittestと全ローカルgate合格。次は`FEEDBACK-EXPORT-001`。
- [x] (2026-08-25 JST) `FEEDBACK-EXPORT-001`: import済みproduction resultから匿名化・決定的・冪等なsignal bundleをatomicに出力するCLI、schema、privacy/reference gateを追加。184 unittestと全ローカルgate合格。次は`KNOWLEDGE-001`。
- [x] (2026-08-25 JST) `KNOWLEDGE-001`: OB/RL/CT/XRのtyped knowledge schema、config語彙、外部profile aesthetic-signal、validator/reference gate、deterministic graph/impact、正常・失敗fixtureを追加。全unittestとvalidator/security/docs/graph gate合格。次は`VISUAL-LANGUAGE-001`。
- [x] (2026-08-25 JST) `VISUAL-LANGUAGE-001`: 媒介・技法・palette/composition・prohibited expressionをtyped visual-language artifactへ固定し、production-translator contextとproduction handoff bundle/schema snapshotへ接続。200 unittestとvalidator/security/docs/graph gate合格。queue上の次タスクはない。
- [x] (2026-08-25 JST) `HARNESS-001`: protocol/work/output rootを分離し、schema-validなatomic/idempotent bootstrap、dependency preflight、protocol provenance、cwd非依存next actionを実装。205 unittestとvalidator/security/docs/graph/diff gate合格。次は`HARNESS-002`。
- [x] (2026-08-25 JST) `HARNESS-002`: provider-neutralなargv worker adapter、versioned attempt request/result schema、bounded/redacted diagnostics、fake worker contractを実装。runtimeを変更せず、timeout/exit/protocol/output-limit/secret/capabilityをnamed failureへ変換し、213 unittestと全ローカルgateが合格。次は`HARNESS-003`。
- [x] (2026-08-25 JST) `HARNESS-003`: task attemptをcanonical projectから隔離し、role write target、safe manifest、changeset、protected root、baseline conflict、quarantineを実装。222 unittestと全ローカルgateが合格。次は`HARNESS-004`。
- [x] (2026-08-25 JST) `HARNESS-004`: role acceptanceをtyped checkへ移行し、attempt validation、changeset promotion、task completeを一つのatomic transactionへ接続。229 unittestと全ローカルgateが合格。次は`HARNESS-005`。
- [x] (2026-08-25 JST) `HARNESS-005`: HUMAN_REQUIREDをtyped human decision requestへ永続化し、list/resolve、新lease resume、replay、stale/replay/option/stateのnamed ruleを実装。237 unittestと全ローカルgateが合格。次は`HARNESS-006`。

チェックボックスとUTCまたはJST日時。未完了、部分完了、完了を正確に表す。

### Surprises & Discoveries

- Production側の現状が`references`/`record_hash`を期待し、Research exporterの`records`/`record_sha256`と不一致だった。canonical hashの計算責任はResearchに置き、Productionは形式と非ゼロ値を検証する。
- Issue #44の実測では、証拠10件・主張5件・インサイト2件・判断2件・要件4件、棄却案0件、先行作品調査0件、自己反復評価0件でも完了・handoffまで到達していた。既存の`COMPLETE_WITH_GAPS`は調査済みの未解決事項を表すため、調査未達とは別の`INCOMPLETE`が必要。
- 2026-08-25: 文書上は出力境界が定義済みだったが、canonicalの`projects/`に実プロジェクト2件と非空graphが残り、validatorは検出していなかった。boundary検査はcanonical rootに限定し、fixture・一時cloneの正当なmaterializationを壊さない。
- 2026-08-25: `.git` indexがsandboxで書けずtracked directoryを`git rm`できなかったため、対象プロジェクトは復元可能な`/private/tmp` quarantineへ移動し、作業ツリー上の削除として検証した。
- 2026-08-25: feedback exportはproduction resultの再importやproject更新を行わず、import済みresultと監査eventのhashを照合して別directoryへ出力する必要がある。提示条件はproduction-result v1にないため、自由文から推測せず`null`固定にした。
- 2026-08-25: profile実体はcanonical output boundaryと衝突するため、`templates/profile`のみをrepositoryへ置き、実profileは`--profiles-root`で外部入力する。graph nodeは`profile/<creator-id>::AS###`としてproject-qualified evidenceへ接続した。
- 2026-08-25: 既存CLIは単一root内のconfig/schema/templateを暗黙に読むため、#69ではwork rootをprotocol assetの自己完結stagingとしてatomic publishする方式を採用した。protocolのGit provenanceはwork rootへコピーせず、常にprotocol rootから取得する。
- 2026-08-25: bootstrapの再実行判定はrequest本文のcanonical SHA-256、run ID、3 rootの組で行う。output rootをbootstrap時に書かないことで、後段の成果物出力と初期化の冪等性を分離した。
- 2026-08-25: worker出力を`communicate()`後に上限判定すると大量stdout/stderrを一時的に全量保持するため、selectorで上限+1 byteまで読み、超過時にprocess groupを停止するbounded readerへ変更した。
- 2026-08-25: role acceptanceは任意shellを正本にせず、gate kind・project-relative target・expected値をschemaで固定した。`next_action`は実行rootをメタデータとして渡し、完了効果はacceptance report hashとchangeset hashの組にした。
- 2026-08-25: `VALIDATION` gate failureはworkerの修正余地を残すためtask-runtimeではretryableに分類した。workerの直接completeはCLI boundaryで拒否し、Python APIの完了はacceptance executorからだけ呼ぶ契約にした。
- 2026-08-25: promotion後の障害に備え、attempt内にtransaction journalとproject preimageを保持した。rollbackはcanonical project全体を復元し、runtime state/logも同じpreimageで戻すため、メモリ上のsnapshotだけに依存しない。
- 2026-08-25: HUMAN_REQUIREDは通知やUIを持たせず、typed journalへの永続化と明示resolve CLIに限定した。同一requestの再送はWAITING_HUMANのno-op、同一responseの再送はPENDINGのno-opとして、leaseとattemptの二重消費を防ぐ。

実装中に判明した制約、失敗、想定との差を、短い証拠とともに記録する。

### Decision Log

- 2026-08-14: Production consumerとの実データ検証でsource-ref indexのwire key不一致が判明したため、Issue #43に従い`references`/`record_hash`へ統一する。参照カテゴリとアクセスURLは別Issueへ分離する。
- 2026-08-16: Issue #44の既定下限は証拠30、主張18、インサイト4、判断3、要件4とする。`research-plan.yaml#minimums`で下げられるが、同じ`minimums.reason`を必須にし、既定値は`config/stopping-policy.yaml`に置く。
- 2026-08-16: 下限・棄却案・不確実性・先行作品・自己反復評価のいずれかが不足した場合、core validationが通っていても完了状態を`INCOMPLETE`とする。`COMPLETE_WITH_GAPS`は調査後に残った非ブロッキングgap専用とし、`INCOMPLETE`のhandoffは生成拒否する。
- 2026-08-16: 先行作品は`03_knowledge/prior-art.jsonl`、自己反復評価は`04_decisions/self-repetition-review.yaml`を正本とし、回答者・PRIVATE_RAW本文・作品実体は記録しない。
- 2026-08-25: RUNTIME-005のpreviewは明示的な`--dry-run`フラグでだけ有効にし、既存のフラグなしlive claim互換性を維持する。未claim previewのleaseはnullで、架空のtoken/expiryを生成しない。
- 2026-08-25: execution queue/stateは旧release完了状態のまま未完了Issueを表現できなかった。RUNTIME-005ではqueueを実行順序の正本として再開し、GitHub open/closed状態や外部Issueをvalidatorの実行条件にしない。
- 2026-08-25: dry-runでlease期限切れや依存失敗を判定するにはliveと同じreconcileが必要だが、永続state/logへ書いてはならない。runtimeのdeep copy上でreconcileし、ready selectorを共有する。
- 2026-08-25: materialization入口の既定rootを残すとcanonical repositoryへ誤出力できるため、`new_project.py`、`run_project.py`、`accept_research_request.py`のCLIは`--root`を必須にする。APIは一時作業rootを明示的に受ける既存契約を維持する。
- 2026-08-25: typed registryはlegacyの`rejected_options`／`uncertainty`と同時に導入し、既存入力を読み取れる状態を保った。双方向検証とgraph edgeはIDフィールドを持つrecordだけへ適用する。
- 2026-08-25: signal bundleはsource resultのschema再検証、import audit hash、acceptance test・requirement・observationの参照解決をすべて通過してから生成し、未知field・PII・private URLは補正せずfail closedする。
- 2026-08-25: knowledge schemaのenumはcommon schemaへ集約し、configとの一致テストに加えてvalidatorのnamed semantic ruleで未知語彙をblockingにした。schemaだけでは表現できない自己relationship、qualified profile reference、期間順序はvalidatorで拒否する。
- 2026-08-25: visual languageの空テンプレートはDRAFT〜DECIDINGでは許容するが、READY_FOR_PRODUCTION以降は媒介決定、技法、適用可能なpalette/composition、禁止表現を必須にした。既存の完了判定fixtureはこの契約に合わせて明示的な媒介決定とartifactを持つよう更新した。
- 2026-08-25: worker adapterのstdout/stderrは固定上限付きselector readerで読み、超過時はprocess groupをkillする。resultはworker応答の受入だけを担い、task runtimeのclaim/completeやoutput adoptionは後続タスクへ分離する。
- 2026-08-25: worker write setから`07_runtime`のharness-owned state/logを分離するため、role configに`runtime_targets` namespaceを追加し、attempt changesetは`write_targets`だけを検証する。
- 2026-08-25: canonical checkoutの既存fileがfilesystem由来のhard-link countを持つため、source/protected baselineでは既存linkを読み取り、attempt copy後のworkspaceだけをhard-link拒否対象にした。これによりsnapshot hashは環境依存のinode情報を含まず、worker-created hard-linkはfail closedできる。
- 2026-08-25: acceptance validationの一部 evaluatorはroot内のconfigを読むため、attempt projectだけでなくprotocol configをephemeral validation rootへコピーした。protocolのschema検証とGit正本は引き続き明示的なprotocol rootを参照する。
- 2026-08-25: 人間判断のresponseはrequest hash、run/project/task/attempt identity、option IDをすべて照合し、異なるresponseの上書きを拒否する。APPROVE/REJECT/CANCELではselected_optionを許可しない。

決定、理由、代替案、影響、日付を記録する。

### Outcomes & Retrospective

- 完了: Research側変更はテスト・validator通過済み。別PRで公開し、Production側consumer変更と同時mergeせず、両方のPR URLを親Issueへ記録してから人間mergeを待つ。
- 完了: `COMPLETION-QUALITY-001`のスキーマ、validator、完了判定、handoff拒否、fixture、テストを実装して検証した。PR #46を作成し、GitHub Actionsのvalidateも成功した。人間レビュー・merge待ち。
- 完了 (2026-08-25): `RUNTIME-005`の`peek_next()`、`next_action.py --dry-run`、queue/state validator、正常・失敗fixtureを実装した。171 unittest、validator、security、chaos、docs、graph、offline evaluation、release gateが合格。次の開始点は`BOUNDARY-001`。
- 完了 (2026-08-25): `BOUNDARY-001`で`projects/`をREADMEだけに戻し、`data/dependency-graph.json`を空へ再生成した。canonical rootの実プロジェクト・非空graphを`PROTOCOL-OUTPUT-BOUNDARY`で拒否し、materialization CLIの`--root`必須化、一時root許容テスト、境界文書を追加した。次の開始点は`DECISION-001`。
- 完了 (2026-08-25): `DECISION-001`で`RO###`／`U###` registry schema、判断との双方向blocking検証、4種のgraph edge、決定的executive brief、human bundle接続を追加した。177 unittestとvalidator、security、docs、graph、offline evaluation、release関連gateが合格した。次の開始点は`FEEDBACK-EXPORT-001`。
- 完了 (2026-08-25): `FEEDBACK-EXPORT-001`で`research-signal-export/v1`、`export_feedback_signals.py`、正常・失敗系テスト、operations/schema/extension docsを追加した。exportは`manifest.json`と`signals.jsonl`だけをatomicに作り、同一bytes以外の既存bundleを上書きしない。次の開始点は`KNOWLEDGE-001`。
- 完了 (2026-08-25): `KNOWLEDGE-001`で5 schema、profile外部root、named blocking rules、graph/impact統合、合成正常・失敗fixtureを追加した。canonical treeはprotocol-onlyのままで、次の開始点は`VISUAL-LANGUAGE-001`。
- 完了 (2026-08-25): `VISUAL-LANGUAGE-001`で`visual-language.schema.json`、媒介語彙、空テンプレート、媒介決定の一意性・採択状態・参照・lifecycle検証を追加した。production-translatorの書込対象と受入条件、production-agent bundle、handoff artifact、schema snapshotへ接続し、正常・失敗fixtureを追加した。200 unittest、validator、security、docs、graph、diff checkが合格した。queueの全タスクがDONEとなったため、次の開始点はない。
- 完了 (2026-08-25): `HARNESS-001`で`harness.py bootstrap`、`harness-run.schema.json`、root境界、dependency preflight、protocol provenance、root-aware acceptance/next action、正常・失敗・冪等性テストを追加した。205 unittest、validator、security、docs、graph、diff gateが合格し、次の開始点は`HARNESS-002`。
- 完了 (2026-08-25): `HARNESS-002`でversioned attempt request/result schema、provider-neutral argv adapter、secret/path/lease redaction、bounded output、fake worker、正常・失敗・冪等性・runtime non-mutation testsを追加した。213 unittest、validator、security、docs、graph、diff gateが合格し、次の開始点は`HARNESS-003`。
- 完了 (2026-08-25): `HARNESS-003`でattempt workspace manifest/changeset、role worker/runtime namespace、protected root snapshot、safe file checks、atomic candidate promotion、baseline conflict、quarantine/recoveryを追加した。222 unittest、validator、security、chaos、docs、graph、diff gateが合格し、次の開始点は`HARNESS-004`。
- 完了 (2026-08-25): `HARNESS-004`でtyped acceptance gate/report/transaction schema、全roleのacceptance_checks、explicit-root next action、ephemeral validation、changeset promotionとtask completionのatomic接続、VALIDATION retry、journal/preimage rollback/crash recovery、idempotent effect、直接complete拒否を追加した。229 unittest、validator、security、docs、chaos、graph、diff gateが合格した。次の開始点は`HARNESS-005`。
- 完了 (2026-08-25): `HARNESS-005`で6分類のtyped human decision request/response schema、journal、WAITING_HUMAN lease解放、attempt非消費、resolve後PENDING、CLI list/resolve、runtime replay、context hash伝播、named stale/replay/option/security/state rulesを追加した。237 unittestとvalidator、security、docs、chaos、graph、diff gateが合格した。次の開始点は`HARNESS-006`。

## HARNESS-003 ExecPlan

### Purpose / Big Picture

Issue #71の契約として、workerがcanonical work project、protocol、別project、data、output、runtimeを直接変更できないよう、claim時点のproject snapshotから一意なattempt workspaceを作る。worker終了後はbefore/after manifestからdeterministic changesetを作成し、roleの宣言済みworker write targetだけを許可する。promotionは後続のacceptance gateが呼ぶ明示APIに限定し、baseline conflictをlock下で拒否する。

### Context and Orientation

- workspace and changeset implementation: `tools/attempt_workspace.py`
- typed contracts: `schemas/attempt-workspace-manifest.schema.json`, `schemas/attempt-changeset.schema.json`
- role write/runtime namespace: `config/task-roles.yaml`, `tools/next_action.py`
- protected content policy: `config/access-policy.yaml`, `tools/security_check.py`
- tests: `tests/test_attempt_workspace.py`

### Plan of Work

1. safe filesystem snapshot、path normalization、symlink/device/socket/hard-link/size/secret checks、Unicode NFC + case collision検出を共通化する。
2. `run/task/attempt`単位のstaging workspaceへprojectをcopyし、baseline/protected-root manifestをatomicに保存する。同じID・baselineは再利用し、異なる入力は拒否する。
3. before/afterからADD/MODIFY/DELETE/RENAME changesetをdeterministically生成し、roleのworker write set、file mode、security policy、schemaをfail closedで検証する。
4. protected rootsの変更検出、baseline lock、candidate staging、promotion、crash quarantine/recoveryを実装する。worker runtime mutationやcanonical direct mutationはpromoteしない。
5. role config、next action、docs、正常/失敗/secret/large/symlink/hard-link/traversal/collision/delete/rename/concurrent/idempotence testsを更新し、全gateを通す。

### Concrete Steps

```bash
.venv/bin/python -m unittest tests.test_attempt_workspace -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/chaos_check.py
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/build_graph.py --check
git diff --check
```

### Validation and Acceptance

- every role's declared worker file target yields a valid deterministic changeset; `runtime_targets` remain harness-owned and cannot be promoted by the worker;
- protocol/README/AGENTS/config/schemas, canonical project, another project, data, output root, and runtime changes are rejected with `ATTEMPT-WRITE-BOUNDARY` or the more specific unsafe/security rule;
- symlink, hard-link, device/socket, path traversal, case/Unicode collision, forbidden/private/secret, and size violations fail closed; allowed changes are not partially promoted when any sibling change is rejected;
- same run/task/attempt and baseline reuses the same workspace/manifest bytes; crashes can be quarantined and recovered without changing canonical project; concurrent canonical changes produce `ATTEMPT-BASELINE-CONFLICT` without overwrite;
- all required unittest, validate, security, chaos, docs, graph, and diff gates pass.

### Idempotence and Recovery

Attempt directories are keyed by `run_id/task_id/attempt_id`. Workspace creation is staged and atomically published. Rejected or crashed attempts remain outside the canonical project and can be moved to a run-scoped quarantine. Promotion verifies baseline under a project lock and prepares a candidate tree before replacing the project; an existing result or different baseline is never silently overwritten.

### Interfaces and Dependencies

- Python: `attempt_workspace.create_attempt_workspace`, `snapshot_project`, `build_changeset`, `promote_attempt`, `quarantine_attempt`
- schemas: `attempt-workspace-manifest.schema.json`, `attempt-changeset.schema.json`
- issue: `agentic-art-research#71`; dependency: `HARNESS-001`; next task after completion: `HARNESS-004` / Issue #72

完了した動作、未完了、教訓、次の計画への影響を記録する。

### Context and Orientation

関係ファイル、正本、用語、現状の動作を説明する。

### Plan of Work

依存順に、変更するファイルと実装内容を書く。

### Concrete Steps

作業ディレクトリ、正確なコマンド、期待する出力を書く。

### Validation and Acceptance

利用者視点の動作と自動テストの両方を定義する。

### Idempotence and Recovery

再実行可能性、部分失敗からの再開、戻してはいけない変更を書く。

### Interfaces and Dependencies

公開CLI、ファイル形式、関数、外部依存と版を明記する。

## COMPLETION-QUALITY-001 ExecPlan

### Purpose / Big Picture

調査量が不足した状態で制作計画へ流れないようにする。`tools/complete.py`は既定下限と定性要件を満たさないプロジェクトを`INCOMPLETE`として非0で終了し、`tools/build_handoff.py`は`INCOMPLETE`プロジェクトのhandoff生成を拒否する。一方、調査済みだが非ブロッキングの未解決事項は従来どおり`COMPLETE_WITH_GAPS`として扱う。

### Plan of Work

1. `config/stopping-policy.yaml`にcompletion minimumsと必須記録要件を追加し、`research-plan.yaml#minimums`のスキーマと下限引き下げ理由を検証する。
2. `prior-art`と`self-repetition-review`のschema、空テンプレート、正本ファイルを追加し、validatorとproject bootstrapへ接続する。
3. `completion-report`、vocabulary、state machine、`complete.py`へ`INCOMPLETE`と質的・量的判定を追加する。
4. `build_handoff.py`で`INCOMPLETE`を明示的に拒否し、正常系・不足系・override系・handoff拒否系をテストする。
5. docs、task queue、state、Issue #44の受入結果を更新する。

### Validation and Acceptance

- 不足fixtureで`python3 tools/complete.py project/<slug>`が`INCOMPLETE`を出力し、非0終了する。
- 既定下限と定性要件を満たすfixtureは`COMPLETE`になり、既存の調査済みgap fixtureは`COMPLETE_WITH_GAPS`のままになる。
- `minimums`を既定より下げる場合、理由なしはvalidatorで失敗し、理由ありは通る。
- `prior-art.jsonl`と`self-repetition-review.yaml`の正常・不正入力がvalidatorで判定される。
- `INCOMPLETE`のプロジェクトから`build_handoff.py`はhandoffを生成しない。
- `python3 -m unittest discover -s tests -v`、`python3 tools/validate.py --check`、security/docs/release gatesが通る。

### Idempotence and Recovery

完了判定の再実行は同じ入力から同じreportを返し、`INCOMPLETE`から勝手に`COMPLETE_WITH_GAPS`へ補正しない。handoff拒否時は既存の`production-handoff.yaml`を変更せず、記録不足を canonical project data に追加してから再実行する。

### Interfaces and Dependencies

- `config/stopping-policy.yaml#defaults.completion_minimums`
- `01_planning/research-plan.yaml#minimums`（任意。下限を下げる場合は`minimums.reason`必須）
- `03_knowledge/prior-art.jsonl`（JSONL collection `prior_art`）
- `04_decisions/self-repetition-review.yaml`（YAML collection `reviews`）
- completion report status `INCOMPLETE`

## RUNTIME-005 ExecPlan

### Purpose / Big Picture

live claimと同じready selectorを使って、実行エージェントが作業開始前に次taskと必要contextを確認できるread-only previewを追加する。同時に、repository-levelの`execution/task-queue.yaml`と`execution/state.yaml`を、未完了Issueを含む実行順序のSSOTとして検証する。

### Plan of Work

1. `tools/task_runtime.py`へdeep-copy reconcileを使うpublic `peek_next()`を追加する。
2. `tools/next_action.py`へ`--dry-run`を追加し、preview/liveのtask selectorとcontextを一致させる。
3. `tools/validate.py`へqueue/stateの存在、DAG、status、dependency、next_task、terminal、last_completed、RUNTIME task欠落のblocking ruleを追加する。
4. queueへ`RUNTIME-005`と#66/#45/#47/#48/#49のtaskを追加し、stateの次の開始点を`BOUNDARY-001`へ移す。
5. preview非変更性、preview/live一致、queue/state正常系・named failure tests、運用文書を更新する。

### Concrete Steps

```bash
.venv/bin/python tools/next_action.py project/<slug> \
  --worker <worker-id> \
  --now 2026-08-25T00:00:00+09:00 \
  --dry-run \
  --root <temporary-root>
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
```

未claim previewは`TASK_PREVIEWED`、有効leaseの再開previewは`TASK_RESUME_PREVIEW`、予算超過は`BUDGET_EXCEEDED`、readyなしは`NO_TASK_READY`を返す。previewはproject rootのbytes/mtimeとruntime eventを変更しない。

### Validation and Acceptance

- `peek_next()`はliveと同じ`_ready()`・reconcile結果から最低IDを選ぶ。
- previewと同じworker/nowのliveでtask ID、role、context、write targets、acceptanceが一致する。
- 未claim previewのleaseはnullで、token/expiry/eventを生成しない。
- queue/state validatorは未知dependency、循環、依存未完了READY、複数IN_PROGRESS、誤next_task、早すぎるterminal、非DONE last_completed、RUNTIME-005欠落をnamed ruleで拒否する。
- `python3 -m unittest discover -s tests -v`と`python3 tools/validate.py --check`を含む全gateが合格する。

### Idempotence and Recovery

previewはstate/logのdeep copyだけを変更し、明示output以外へ書き込まない。live claimとの競合時は再度snapshotを読み、黙って別taskのcontextを返さない。queue/state validatorは自動修正しない。途中停止時は`execution/state.yaml#resume_from`から`BOUNDARY-001`を再開する。

### Interfaces and Dependencies

- public Python: `task_runtime.peek_next(root, target, worker_id, *, now, lease_seconds=None)`
- CLI: `tools/next_action.py ... [--dry-run]`
- queue SSOT: `execution/task-queue.yaml`
- state SSOT: `execution/state.yaml`
- 先行task: `RUNTIME-004`
- 後続開始点: `BOUNDARY-001`（Issue #66）

## BOUNDARY-001 ExecPlan

### Purpose / Big Picture

canonical repositoryをprotocol-onlyの正本へ戻し、実プロジェクトとproject-derived graphがGitへ入る経路をblockingにする。利用者は`python3 tools/validate.py --check`と`python3 tools/security_check.py --check`で`PROTOCOL-OUTPUT-BOUNDARY`が検査され、`python3 tools/build_graph.py --check`で空のgraphが確認できる。実プロジェクトは明示した一時作業rootで生成し、検証後に外部出力先へ移す。

### Plan of Work

1. canonical `projects/`の実プロジェクトを隔離し、`data/dependency-graph.json`を空の決定的graphへ再生成する。
2. advanced security scannerへ`projects/`と`data/`の許可ファイル、空graph、`PROTOCOL-OUTPUT-BOUNDARY`のblocking契約を追加する。canonical root以外は一時作業rootとして許容する。
3. materializing CLIの`--root`を必須化し、README、operations、boundary docs、`.gitignore`をsafe execution flowへ揃える。
4. queue/stateを更新し、依存完了済みの`DECISION-001`、`FEEDBACK-EXPORT-001`、`KNOWLEDGE-001`をREADYにする。

### Concrete Steps

```bash
.venv/bin/python tools/build_graph.py
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/build_graph.py --check
.venv/bin/python -m unittest tests.test_security_check tests.test_release_check
```

canonical rootの`projects/`にはREADME以外、`data/`にはREADMEと空の`dependency-graph.json`以外を置かない。実プロジェクトのfixture検証は一時rootで実行し、canonical rootへの直接materializationはCLIが`--root`不足として拒否する。

### Validation and Acceptance

- canonical tracked projects/data contain protocol-only files。
- non-emptyまたはinvalidなcanonical graph、canonical project output、許可外data fileが`PROTOCOL-OUTPUT-BOUNDARY`で拒否される。
- 一時作業rootのprojectはsecurity/validatorで正常に検証できる。
- `python3 tools/security_check.py --check`、`python3 tools/validate.py --check`、`python3 tools/build_graph.py --check`、全unittest、release gateが合格する。

### Idempotence and Recovery

graph再生成は同じ空入力からbyte-identicalになる。隔離した旧プロジェクトは`/private/tmp`のquarantineから復元でき、canonicalへ戻す前にはboundary検査を再実行する。validatorとsecurity scannerは自動修正せず、違反をnamed findingとして返す。

### Interfaces and Dependencies

- boundary rule: `PROTOCOL-OUTPUT-BOUNDARY`
- scanner: `tools/security_check.py#scan_advanced_security`
- repository validator: `tools/validate.py#validate_repository`
- generated graph: `data/dependency-graph.json`
- materialization CLIs: `tools/new_project.py`、`tools/run_project.py`、`tools/accept_research_request.py`（`--root`必須）
- next task: `DECISION-001`（Issue #48）

## DECISION-001 ExecPlan

### Purpose / Big Picture

棄却案と不確実性を判断の文字列付属物からtyped registryへ昇格し、判断・registry・制作要件を同じ正本から人間が確認できるようにする。`tools/validate.py --check`がIDの存在と双方向参照をblocking検証し、`tools/executive_brief.py`が`04_decisions/executive-brief.md`を決定的に生成し、`tools/build_graph.py`とhuman bundleが同じ関係を利用する。

### Plan of Work

1. `rejected-option`／`uncertainty` schemaとテンプレートの検証接続を追加し、decision schemaへtyped IDを後方互換で追加する。
2. validatorへtyped registryのforward/reverse参照検証を追加し、片方向・未知IDをnamed blocking findingにする。
3. graphへ棄却案・不確実性のノードと双方向edgeを追加し、deterministic executive briefを生成するCLIを実装する。
4. project bootstrap、offline fixture、request受理、human bundle、docsをbrief生成に接続する。
5. 正常・失敗テスト、queue/state、全ローカルgateを更新し、次の`FEEDBACK-EXPORT-001`へ再開点を移す。

### Concrete Steps

```bash
.venv/bin/python -m unittest tests.test_schemas tests.test_validation tests.test_bundle tests.test_executive_brief tests.test_graph
.venv/bin/python tools/executive_brief.py project/<slug> --root <temporary-work-root>
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
```

### Validation and Acceptance

- typed `RO###`／`U###` recordsと`DC###`のIDがschemaで検証される。
- forwardまたはreverseの片方が欠けるtyped registryは`DECISION-REGISTRY-REVERSE`で失敗する。
- graphは`rejects`、`rejected_by`、`has_uncertainty`、`uncertainty_of`を決定的に含める。
- 同じcanonical projectを同じ入力でbrief生成するとbyte-identicalになり、human bundleにbriefが含まれる。
- legacy文字列フィールドを含む既存fixtureは引き続き検証できる。
- 全unittest、validator、security、docs、graph、offline evaluation、release gateが合格する。

### Idempotence and Recovery

brief生成はcanonical sourceを読み取り、同一内容をatomic writeする。生成物を直接編集しても正本は変わらず、判断やregistry変更後の再生成で復旧できる。typed registryの相互参照は自動修正せず、validatorが不足IDと修正方向を報告する。

### Interfaces and Dependencies

- schemas: `schemas/rejected-option.schema.json`、`schemas/uncertainty.schema.json`、`schemas/decision.schema.json`
- validator: `tools/validate.py#_check_bidirectional_decision_registries`
- brief CLI: `tools/executive_brief.py project/<slug> --root <temporary-work-root>`
- generated artifact: `04_decisions/executive-brief.md`
- next task: `FEEDBACK-EXPORT-001`（Issue #45）

## FEEDBACK-EXPORT-001 ExecPlan

### Purpose / Big Picture

import済みproduction resultをproject-local監査から再利用可能なresearch signalへ変換する。`tools/export_feedback_signals.py`は、入力projectを変更せず、`research-signal-export/v1`に適合する`manifest.json`と`signals.jsonl`を明示outputへ生成する。外部配送は行わない。

### Plan of Work

1. `schemas/research-signal-export.schema.json`を追加し、manifestとsignal recordの型、ID、hash、提示条件null契約を固定する。
2. import log、`PRODUCTION_FEEDBACK_IMPORTED` audit、production-owned result schemaを再検証するread-only loaderを実装する。
3. acceptance test・requirement・observationをID解決してdeterministic JSON/JSONLへ変換し、PII、secret、private URL、未知fieldをfail closedする。
4. output boundary、atomic rename、同一bytesの`ALREADY_EXPORTED`、異なる既存bundleのconflictを実装する。
5. 正常・失敗テスト、docs、queue/state、全gateを更新する。

### Concrete Steps

```bash
.venv/bin/python -m unittest tests.test_export_feedback_signals tests.test_schemas -v
.venv/bin/python tools/export_feedback_signals.py project/<slug> \
  --result-id PR001 --output <signal-bundle-directory> --root <working-root>
.venv/bin/python tools/validate.py --check
.venv/bin/python -m unittest discover -s tests -v
```

### Validation and Acceptance

- import済みresultだけがexport対象になり、result hashとaudit hashが一致する。
- acceptance test、requirement、関連observationが解決され、signal recordは1 acceptance testにつき1件になる。
- 同じ入力・異なるrootでbundle bytesが一致し、`presentation_conditions`はnull固定になる。
- 未import、hash不一致、未知field、未解決ID、PII/private URLはnamed ruleで失敗し、partial bundleを残さない。
- 同一outputは`ALREADY_EXPORTED`、異なる内容は`FEEDBACK-EXPORT-CONFLICT`で非破壊に失敗する。
- project root、canonical `projects/`、`data/`、外部repositoryへ暗黙に書き込まない。

### Idempotence and Recovery

project入力はread-onlyで、bundleは一時directoryで全検証してからatomic renameする。失敗時はtemporary directoryを破棄し、既存bundleは変更しない。既存bundleの同一bytesだけを成功扱いし、異なるbytesは上書きしない。

### Interfaces and Dependencies

- schema: `schemas/research-signal-export.schema.json`
- CLI: `tools/export_feedback_signals.py project/<slug> --result-id PR001 --output <dir> --root <root>`
- source: `07_runtime/production-feedback-imports.jsonl`、`07_runtime/run-log.jsonl`、`05_production/acceptance-tests.yaml`、`05_production/production-requirements.yaml`
- next task: `KNOWLEDGE-001`（Issue #47）

## KNOWLEDGE-001 ExecPlan

### Purpose / Big Picture

`03_knowledge/`のobservation、relationship、contradiction、external-referenceを型付き・参照解決可能にし、profile由来のaesthetic signalを実projectの証拠へ追跡可能な形でdependency graphへ統合する。実project/profile instanceはcanonical repositoryへ置かない。

### Plan of Work

1. 5つのDraft 2020-12 schemaとconfig vocabularyを追加し、正常・required-field欠落fixtureを固定する。
2. JSONL validatorへ4 knowledge recordを登録し、endpoint/reference、重複、自己relationship、語彙、contradiction resolutionをblocking検査する。
3. `templates/profile`を追加し、`--profiles-root`からだけaesthetic signalを読み、qualified evidence、期間、語彙を検証する。
4. 5 node kindと参照edgeをdeterministic graphへ追加し、既存impact traversalでCT/ASを検証する。
5. operations/schema/design docs、queue/state、正常・失敗テスト、全gateを更新する。

### Concrete Steps

```bash
.venv/bin/python -m unittest tests.test_knowledge tests.test_schemas tests.test_graph tests.test_validation -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/build_graph.py --check
.venv/bin/python -m unittest discover -s tests -v
```

### Validation and Acceptance

- OB/RL/CT/XR/ASの最小valid recordがschemaとvalidatorを通過する。
- 未解決参照、重複ID、自己relationship、未知語彙、RESOLVED contradictionのresolution欠落、profile期間逆転、qualified evidence欠落がnamed findingになる。
- graph node/edgeは同一入力でbyte-identicalで、CT/ASから参照先へ到達し、無関係nodeへは到達しない。
- profile instance、実project、project-derived graphはcanonical treeへ追加されない。

### Idempotence and Recovery

validatorとimpactはread-only。graphは入力を変更せず、project-qualified/profile-qualified keyとsorted outputで再生成する。profile rootの不正入力は補正せず、source pathとfieldを示して失敗する。

### Interfaces and Dependencies

- schemas: `schemas/observation.schema.json`、`schemas/relationship.schema.json`、`schemas/contradiction.schema.json`、`schemas/external-reference.schema.json`、`schemas/aesthetic-signal.schema.json`
- validator: `tools/validate.py --profiles-root <profile-root>`
- graph/impact: `tools/build_graph.py --profiles-root <profile-root>`、`tools/impact.py --profiles-root <profile-root>`
- template: `templates/profile/aesthetic-signals.yaml`
- next task: `VISUAL-LANGUAGE-001`（Issue #49）

## VISUAL-LANGUAGE-001 ExecPlan

### Purpose / Big Picture

調査判断から制作へ渡す視覚言語を、自由文だけでなく検証可能なtyped artifactとして固定する。`05_production/visual-language.yaml`は媒介、技法、palette、composition、禁止表現、不確実性を決定へ戻せる形で保持し、`tools/validate.py --check`がREADY_FOR_PRODUCTION以降の未完了・未参照・未採択状態をblockingにする。production-translator context、production-agent bundle、handoff exportには同じartifactとschema snapshotが含まれる。

### Plan of Work

1. `config/vocabularies.yaml`、`schemas/common.schema.json`、`schemas/visual-language.schema.json`、project manifest/templateへ媒介語彙、適用可否、visual-language entry pointを追加する。
2. `tools/validate.py`へ、明示的な媒介決定の0件・複数件・未採択、decision/requirement/uncertainty参照、重複・適用可否、禁止表現、lifecycle completenessのnamed blocking rulesを追加する。
3. production-translator role、context pack、production-agent bundle、handoff export、release schema listへartifactとschema snapshotを接続する。
4. schema/semantic/handoff/completion/sample fixtureと正常・失敗テストを追加し、既存完了判定fixtureをREADY_FOR_PRODUCTION契約へ合わせる。
5. README、schema reference、operations、設計仕様、handoff extension、実行計画、queue/stateを更新し、全ローカルgateを実行する。

### Concrete Steps

```bash
.venv/bin/python -m unittest tests.test_visual_language tests.test_schemas tests.test_handoff_build tests.test_context_pack tests.test_complete tests.test_sample -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/build_graph.py --check
git diff --check
```

### Validation and Acceptance

- DRAFT〜DECIDINGの空テンプレートはschemaを通過し、READY_FOR_PRODUCTION以降の空・不完全artifactは`VISUAL-LANGUAGE-COMPLETENESS`で拒否される。
- 媒介決定が0件、複数件、未採択、許可語彙外、またはartifactのsource decisionと不一致の場合、`VISUAL-LANGUAGE-MEDIUM-DECISION`または`VISUAL-LANGUAGE-REFERENCE`で拒否される。媒介は自由文から推測されない。
- decisionはADOPTED、requirementはRQ、uncertaintyはOPENまたはACCEPTED_RISKへ解決され、重複preferred/prohibited、重複ID、不正なNOT_APPLICABLE内容がnamed ruleで拒否される。
- READY_FOR_PRODUCTION以降はtechniqueとprohibited expressionが各1件以上あり、APPLICABLEセクションはpreferred・rationale・source decisionを持つ。
- production-translator context、production-agent bundle、handoff exportにvisual-language artifactが入り、handoff bundleに`visual-language.schema.json` snapshotが入る。
- `python -m unittest discover -s tests -v`（200 tests）、`tools/validate.py --check`、security/docs/build_graph gate、`git diff --check`が合格する。

### Idempotence and Recovery

schema、validator、context、bundle、handoff exportはcanonical sourceを読み取り、入力を補正しない。bundle/exportは既存成果物を異なる内容で上書きせず、visual-languageの不整合は入力ファイルとnamed ruleを示して停止する。完了判定fixtureの変更は、READY_FOR_PRODUCTIONに必要な明示的な媒介決定とartifactを追加するだけで、検証要件を弱めない。

### Interfaces and Dependencies

- schema: `schemas/visual-language.schema.json`
- project artifact: `05_production/visual-language.yaml`
- vocabulary: `config/vocabularies.yaml#medium_types`, `#visual_language_applicabilities`
- validator: `tools/validate.py#_check_visual_language`
- context: `tools/context_pack.py` production-translator source set
- bundle/export: `tools/bundle.py`, `tools/export_handoff.py`
- dependency: `DECISION-001`（typed decision/registry）
- issue: `agentic-art-research#49`

## HARNESS-001 ExecPlan

### Purpose / Big Picture

Issue #69のfoundationとして、protocol repository、temporary work root、external output rootを明示的に分離する。`tools/harness.py bootstrap`をrequestまたはslug/titleから実行すると、protocolのGit provenance付きrun manifest、project、`07_runtime.task_runtime`をwork rootへ作成し、output rootは空のままにする。既存の単一`--root` CLIは互換shimとして残すが、harness内部の入口はroot-aware APIを使う。

### Context and Orientation

- root contract: `tools/harness_paths.py`
- bootstrap CLI and run manifest: `tools/harness.py`, `schemas/harness-run.schema.json`
- request/project materialization: `tools/accept_research_request.py`, `tools/new_project.py`
- root-aware validation/graph/context/entrypoint: `tools/validate.py`, `tools/build_graph.py`, `tools/context_pack.py`, `tools/next_action.py`
- protocol provenance for handoff: `tools/handoff_common.py`, `tools/build_handoff.py`, `tools/export_handoff.py`
- normal and failure contract: `tests/test_harness.py`, `tests/fixtures/harness/request.yaml`

### Plan of Work

1. 共通root境界、run schema、optional dependency preflightを追加する。
2. requestまたはslug/titleを一時stagingへmaterializeし、runtime初期化・validation後にwork rootへatomic publishするbootstrapを追加する。
3. request hash/run ID/rootの再実行をbyte-identical resumeとし、非空・別入力・symlink・親子rootをnamed conflictで拒否する。
4. protocol/work/outputのroot-aware CLI/APIと、cwd非依存のacceptance/next stepを既存互換を保って接続する。
5. docs、queue/state、正常・失敗・冪等性テストを更新し、全release gateを通す。

### Concrete Steps

```bash
.venv/bin/python tools/harness.py bootstrap \
  --request tests/fixtures/harness/request.yaml \
  --protocol-root . \
  --work-root "$(mktemp -d)" \
  --output-root "$(mktemp -d)" \
  --run-id HR001 \
  --now 2026-08-25T00:00:00+09:00
.venv/bin/python -m unittest tests.test_harness tests.test_research_request tests.test_next_action -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/build_graph.py --check
git diff --check
```

### Validation and Acceptance

- bootstrap output and `.harness/run.json` are valid `harness-run.schema.json` documents, contain the protocol commit, project/run IDs, all roots, and AVAILABLE/MISSING/INVALID dependency preflight.
- a successful request run creates `projects/<slug>/07_runtime/research-state.json` with `task_runtime`; protocol bytes/mtimes and output root remain unchanged/empty.
- same request hash, run ID, and roots return the exact existing JSON; different content, non-empty target, same work/output, symlink, nested, or broad roots fail with `HARNESS-BOOTSTRAP-CONFLICT` or `HARNESS-ROOT-BOUNDARY` without partial mutation.
- root-aware next action emits explicit protocol/work/output context and acceptance commands executable independent of cwd; handoff provenance reads Git state from protocol root only.
- all unittest, validator, security, docs, graph, and diff gates pass.

### Idempotence and Recovery

The work root is built in a sibling staging directory and published only after validation. A failure removes only that staging directory and leaves the supplied empty work/output directories unchanged. Existing run manifests are never overwritten. If a later worker has populated output, rerunning bootstrap reads the existing work manifest before checking output emptiness and returns the same run JSON.

### Interfaces and Dependencies

- CLI: `tools/harness.py bootstrap --request|--slug --protocol-root --work-root --output-root --run-id`
- Python: `harness_paths.HarnessPaths`, `harness.bootstrap(...)`
- schema: `schemas/harness-run.schema.json`
- optional dependency flags: `--profiles-root`, `--art-history-root`, `--production-schema`
- issue: `agentic-art-research#69`; next task: `HARNESS-002` / Issue #70

## HARNESS-002 ExecPlan

### Purpose / Big Picture

Issue #70の契約として、provider名やSDKをprotocolへ固定せず、1 task attemptを外部worker subprocessへ安全に渡し、schema-validなstructured resultだけを返す。adapterは`task_runtime`、project正本、output rootを変更しない。`fake` adapterとfixtureで正常系、timeout、exit、signal、protocol、output limit、secret、capability、command拒否をオフライン再現する。

### Context and Orientation

- attempt request/result contracts: `schemas/agent-attempt-request.schema.json`, `schemas/agent-attempt-result.schema.json`
- provider-neutral command policy: `config/worker-adapters.yaml`
- adapter implementation: `tools/worker_adapter.py`
- deterministic worker fixture: `tests/fixtures/workers/fake_worker.py`
- contract tests: `tests/test_worker_adapter.py`, `tests/test_attempt_protocol.py`
- runtime boundary: `tools/task_runtime.py`; adapter must not import or call mutating runtime operations

### Plan of Work

1. Request/result JSON Schemaとadapter設定で、lease、context、instructions、write targets、acceptance、workspace、deadline、capabilities、status、failure、human decisionをtyped contractにする。
2. `shell=False`のargv起動、stdin JSON、stdout single-result JSON、bounded stderr、timeout/process-group termination、exit/signal/protocol/output-limitをdeterministic resultへ変換する。
3. config-declared environmentだけを渡し、access-policy patternとworker result内のlease/path/credential漏洩をfail closedで検出・redactする。
4. fake workerと正常・失敗・再実行・runtime non-mutation tests、runtime/schema/security docsを追加する。
5. 全unittest、validator、security、docs、graph、diff gateを実行し、queue/stateを次のREADY taskへ更新する。

### Concrete Steps

```bash
.venv/bin/python tools/worker_adapter.py run \
  --request tests/fixtures/harness/attempt-request.json \
  --adapter fake --protocol-root .
.venv/bin/python -m unittest tests.test_worker_adapter tests.test_attempt_protocol -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/build_graph.py --check
git diff --check
```

### Validation and Acceptance

- normal fake result is valid against `agent-attempt-result.schema.json` and repeated identical requests produce byte-identical results;
- command is an argv array under `shell=False`; shell interpreters/metacharacter command forms, missing executable, unsupported capability, expired deadline, and non-empty/invalid config fail with named rules;
- timeout, nonzero exit, signal, invalid JSON, unknown result field, stdout/stderr limits, and secret output each return schema-valid `FAILED` results with bounded diagnostics and no raw secret, lease token, absolute path, or credential value;
- valid worker `HUMAN_REQUIRED` results pass through as typed results; adapter never mutates task state, project runtime, canonical protocol, or output root;
- all required release gates pass.

### Idempotence and Recovery

Result bytes are a deterministic function of the request, adapter configuration, and bounded worker response. An optional output file is atomically created only when absent; an identical existing result is reused and a different result is rejected without overwrite. A killed or timed-out worker is terminated with its process group and leaves no runtime mutation; a later supervisor may retry using the same attempt protocol.

### Interfaces and Dependencies

- CLI: `tools/worker_adapter.py run --request <json> --adapter <name> --protocol-root <root> [--command-json <argv-json>]`
- Python: `worker_adapter.run_attempt(...)`
- schemas: `agent-attempt-request.schema.json`, `agent-attempt-result.schema.json`
- issue: `agentic-art-research#70`; dependency: `HARNESS-001`; next task after completion: `HARNESS-003` / Issue #71

## HARNESS-004 ExecPlan

### Purpose / Big Picture

Issue #72の契約として、roleごとの受入条件を任意shell commandからversioned typed gateへ移行し、
attempt projectの検証、changeset promotion、task runtimeの`SUCCEEDED`を一つのハーネス境界で実行する。
失敗時はcanonical projectを変更せず、成功後の障害もtransaction preimageから復元できるため、workerは
直接完了を宣言できない。

### Context and Orientation

- typed gate/report/transaction contracts: `schemas/acceptance-gate.schema.json`, `schemas/acceptance-report.schema.json`, `schemas/acceptance-transaction.schema.json`
- role gate source: `config/task-roles.yaml#roles.*.acceptance_checks`
- executor and transaction boundary: `tools/acceptance_executor.py`
- typed handoff: `tools/next_action.py`
- task completion boundary: `tools/task_runtime.py`
- attempt promotion: `tools/attempt_workspace.py`
- contract tests: `tests/test_acceptance_executor.py`, `tests/test_next_action.py`, `tests/test_harness.py`

### Progress

- [x] role acceptanceをtyped gateへ移行し、legacy shell acceptanceを拒否する。
- [x] attempt projectをephemeral validation rootで評価し、reportをdeterministic/idempotentに保存する。
- [x] changeset promotion、post-promotion validation、task completeをtransaction journalとpreimageで接続する。
- [x] gate failure、unknown/absolute target、直接CLI complete、completion fault rollbackをテストする。
- [x] 全release gateを実行し、queue/state/ExecPlanを完了状態へ更新してcommitする。

### Plan of Work

1. gate schemaでkind、relative target、expected値、reportのnamed ruleとsecret-free出力を固定する。
2. role tableの全acceptanceをtyped checkへ移行し、`next_action`は明示root付きdictだけを返す。
3. acceptance executorをattempt snapshotへ接続し、全gate成功時だけchangesetをcandidate promotionする。
4. promotion後のblocking validationとtask runtime completeを実行し、失敗時はpreimage復元、journal更新、named rollbackを行う。
5. 同一report/changesetの再送、未知kind、絶対path、missing target、baseline conflict、failure retryを検証する。
6. docs、queue/state、Progress/Discoveries/Decision Log/Outcomesを更新し、全gateを通す。

### Concrete Steps

```bash
.venv/bin/python -m unittest tests.test_acceptance_executor tests.test_next_action tests.test_harness -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/chaos_check.py
.venv/bin/python tools/build_graph.py --check
git diff --check
```

### Validation and Acceptance

- 全roleのacceptanceはtyped gateのみで、legacy shell command、unknown kind、absolute/path traversal targetをnamed ruleで拒否する。
- `next_action`のacceptanceとon-completionにはshell complete commandがなく、protocol/work/output/project IDが明示される。
- 受入reportはschema-validで、gate ID/status/rule/remediation/target/expected/actualを持ち、同じ入力と時刻からbyte-identicalになる。
- 一つでもgateが失敗すればworker変更はcanonicalへ反映されず、taskは`VALIDATION` retryable failureになる。
- 全gate成功時だけchangesetがpromoteされ、blocking validation後にtaskが`SUCCEEDED`となる。既存成功attemptの同一effectは同じ結果を返す。
- promotion後のvalidationまたはcomplete faultではcanonical project、research-state、run-logがpreimageへ復元され、`ACCEPTANCE-ROLLBACK`を返す。
- unittest、validator、security、docs、chaos、graph、diffの全gateが合格する。

### Idempotence and Recovery

attempt内の`transaction.json`は`PREPARED`、`PROMOTED`、`COMMITTED`、`ROLLED_BACK`を記録し、
`transaction-before/`にpromotion前のproject bytesを保持する。同一attemptの再送は成功済みtaskの
effect keyとreport/changeset hashを照合し、異なる結果は上書きしない。プロセスがpromotion直後に
停止した場合は`recover_attempt_transaction`がjournalとmanifestを照合してpreimageへ戻す。

### Interfaces and Dependencies

- Python: `acceptance_executor.execute_acceptance`, `acceptance_executor.complete_attempt`, `acceptance_executor.recover_attempt_transaction`
- schemas: `acceptance-gate.schema.json`, `acceptance-report.schema.json`, `acceptance-transaction.schema.json`
- CLI boundary: `task_runtime.py complete` requires `--harness-completion` and is not a worker completion API
- issue: `agentic-art-research#72`; dependencies: `HARNESS-001`, `HARNESS-003`; next task: `HARNESS-005` / Issue #73

## HARNESS-005 ExecPlan

### Purpose / Big Picture

Issue #73の契約として、設計仕様§6.2の6分類だけを人間判断へ停止させる。workerの
`HUMAN_REQUIRED` resultからdecision requestを作り、canonical hashとrun/project/task/attemptへ
束縛して永続化する。人間のresponseは`harness.py decisions resolve`で一度だけ適用し、同じtaskを
新leaseの取得可能な状態へ戻す。通知配送、UI、外部送信は行わない。

### Context and Orientation

- request/response contracts: `schemas/human-decision-request.schema.json`, `schemas/human-decision-response.schema.json`, `schemas/human-decisions.schema.json`
- persisted project journal: `templates/project/07_runtime/human-decisions.yaml`
- persistence and hash binding: `tools/human_decisions.py`
- state transition/replay: `tools/task_runtime.py`
- CLI and HUMAN_REQUIRED boundary: `tools/harness.py`, `tools/worker_adapter.py`
- context handoff/validation: `tools/context_pack.py`, `tools/next_action.py`, `tools/validate.py`
- tests: `tests/test_human_decisions.py`, `tests/test_worker_adapter.py`

### Progress

- [x] §6.2の6分類、request/response schema、vocabulary、journal templateを固定した。
- [x] HUMAN_REQUIREDをhash・identity・security付きrequestへ正規化し、WAITING_HUMANでleaseを解放した。
- [x] list/resolve CLI、PENDING再開、runtime replay、contextへのresponse hash伝播を実装した。
- [x] 同一request/responseの再送を冪等化し、stale/replay/option/state/securityをnamed ruleで拒否した。
- [x] 全release gateを実行し、queue/state/ExecPlanを次のREADY taskへ更新した。

### Plan of Work

1. §6.2の6カテゴリ、request/response fields、option/action/default-safe-action、hashをschemaとconfig vocabularyへ固定する。
2. HUMAN_REQUIRED resultをrequestへ正規化し、security marker、category、identity、canonical hashを検証してjournalへ保存する。
3. task runtimeへ`WAITING_HUMAN`、lease解放、attempt非消費、resolve後`PENDING`、dependent非terminalを追加する。
4. list/resolve CLI、stale/replay/option/state checks、runtime event replay、response ID/hashをcontext packへ接続する。
5. 正常・失敗・CLI・replay・adapter integration testsとdocsを追加し、全gateを通す。

### Concrete Steps

```bash
.venv/bin/python -m unittest tests.test_human_decisions tests.test_worker_adapter tests.test_task_runtime -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/chaos_check.py
.venv/bin/python tools/build_graph.py --check
git diff --check
```

### Validation and Acceptance

- 未承認category、private marker、absolute path、invalid/missing request fieldsはnamed ruleで拒否される。
- HUMAN_REQUIREDのtaskは`WAITING_HUMAN`、lease null、attempt不変、dependent非terminalとなり、requestが決定的JSON/YAMLへ残る。
- `decisions list`は未解決requestのみをID順で返し、valid resolveはresponse hashとrequest hashを照合してtaskを`PENDING`へ戻す。
- stale hash、別run/project/task/attempt、unknown option、reason/schema欠落、二重resolveをそれぞれnamed ruleで拒否する。同一responseの再送だけは同じ結果を返す。
- run-log replayはWAITING_HUMANとresolve後PENDINGを同じtask runtimeへ再構築し、次のcontextにresponse ID/hashとactionだけを渡す。
- §6.3の人間不要ケースはrequestを生成せず、既存のstopping/completion/gap contractだけを使う。
- 全unittest、validator、security、docs、chaos、graph、diff gateが合格する。

### Idempotence and Recovery

request/responseは自己hashを除いたcanonical payloadのSHA-256で固定する。journalとruntime更新は
失敗時に元のbytesへ戻す。resolve途中の中断はrun-logに残る単一イベントを再利用し、同じresponseの
再適用はno-op、異なるresponseやstale requestは上書きしない。

### Interfaces and Dependencies

- CLI: `tools/harness.py decisions list|resolve project/<slug> --work-root <root> [--response <json>]`
- Python: `human_decisions.record_human_required`, `human_decisions.resolve_request`, `task_runtime.replay_runtime`
- issue: `agentic-art-research#73`; dependencies: `HARNESS-001`, `HARNESS-002`, `HARNESS-004`; next task: `HARNESS-006` / Issue #74

## 実行規則

1. 計画全体を読む。
2. `Progress` と `execution/task-queue.yaml` の整合を確認する。
3. 次の未完了マイルストーンだけを実装する。
4. 小さな動作単位でテストする。
5. 発見と決定を即時に計画へ戻す。
6. 受入条件を満たすまで「完了」としない。
7. 終了時に次の正確な開始点を残す。
