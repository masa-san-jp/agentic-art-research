# Agentic Art Research 制作引き渡し拡張実行計画

- 作成日: 2026-08-11
- 状態: READY
- 対応仕様: `docs/20260811-agentic-art-research-production-handoff-extension-specification.md`
- 基点: `v1.0.1`以降の`main`
- 想定リリース: 後方互換を保てる場合`v1.1.0`

## Purpose / Big Picture

完成後は、実行エージェントが既存の証拠・判断・要件から制作仮説とPrototype Planを生成し、`agentic-art-production`が検証可能なhandoff bundleを決定的に出力できる。制作側の結果は、内容hashと出所を検証して新しい証拠候補として還流できる。

利用者は次で確認する。

```bash
python3 tools/run_project.py harmony-study --offline-fixture tests/fixtures/harmony-handoff
python3 tools/build_handoff.py project/harmony-study
python3 tools/export_handoff.py project/harmony-study --output data/handoffs/harmony-study
python3 tools/import_production_result.py tests/fixtures/production-results/pass.yaml --dry-run
python3 tools/validate.py --check
python3 -m unittest discover -s tests -v
```

## Progress

- [x] (2026-08-11) `EXTENSION-DESIGN-001`: 責任境界、成果物、契約、実装フェーズを確定。
- [ ] `HANDOFF-SCHEMA-001`: schema、語彙、雛形、fixtureを追加。
- [ ] `HANDOFF-VALIDATE-001`: 仮説、Prototype DAG、handoff参照・hash・安全境界を検証。
- [ ] `HANDOFF-BUILD-001`: handoff生成、export、production-agent bundleを実装。
- [ ] `FEEDBACK-IMPORT-001`: production resultのdry-run、取込、冪等性、影響分析を実装。
- [ ] `HANDOFF-E2E-001`: cross-repo fixture、障害系、後方互換性評価を完成。
- [ ] `HANDOFF-RELEASE-001`: 文書、release gate、互換性証拠を完成。

## Surprises & Discoveries

- 2026-08-11: v1.0.1の`requirement.schema.json`は追跡性と受入試験接続を表すが、制作仮説、比較、Prototype Plan、handoffの正式schemaを持たない。
- 2026-08-11: `templates/project/05_production/prototype-backlog.yaml`は空雛形であり、代表fixtureには対応ファイルがない。既存プロジェクトを壊さない移行が必要。
- 2026-08-11: 既存状態`READY_FOR_PRODUCTION`は自然な境界だが、制作側の長期状態を追加するとresearch完了条件と混線する。handoff状態を独立させる。
- 2026-08-11: request/result schemaを片方のrepoだけで共同所有すると変更責任が曖昧になる。生成側を正本、受信側をcommit固定snapshotとする。

## Decision Log

| 日付 | 決定 | 代替案 | 理由・影響 |
|---|---|---|---|
| 2026-08-11 | researchは制作仮説・Prototype Plan・handoffまでを所有 | full production planningを同居 | WBS、予算、制作進捗の周期をresearch状態機械から分離する |
| 2026-08-11 | `workflow_mode`未指定を`RESEARCH_ONLY`として扱う | 全既存projectにhandoffを必須化 | v1 fixtureと利用契約を維持する |
| 2026-08-11 | handoff request schemaはresearchが正本 | 第三のcontract repo | 二repo段階で運用対象を増やさない |
| 2026-08-11 | production resultは証拠候補として取込 | 判断・要件を直接更新 | 実施条件と不確実性を保持する |
| 2026-08-11 | handoffのpayload hashを必須化 | commit SHAだけ | export後の内容改変を検出する |

## Outcomes & Retrospective

設計段階では、v1の追跡グラフを壊さずにproductionとの双方向境界を追加する方針を確定した。実装完了時に、観察可能な動作、未完了、互換性、release判断を追記する。

## Context and Orientation

### 現行正本

- 基礎仕様: `docs/20260811-agentic-art-research-system-design-specification.md`
- 拡張仕様: `docs/20260811-agentic-art-research-production-handoff-extension-specification.md`
- 実行順序: `execution/task-queue.yaml`
- 語彙: `config/vocabularies.yaml`
- schema: `schemas/`
- 雛形: `templates/project/`
- validator: `tools/validate.py` と `tools/validation/`
- runtime: `tools/run_project.py` と`tools/runtime/`
- traceability: `tools/build_graph.py`、`tools/impact.py`、`tools/bundle.py`

### 接続先契約

- production設計仕様: `masa-san-jp/agentic-art-production/docs/20260811-agentic-art-production-system-design-specification.md`
- handoff request schema正本: 本repo
- production result schema正本: production repo
- cross-repo検証: 各repoの固定fixtureを用い、ネットワークなしで実行する

### 実装中に守る不変条件

1. 既存v1 fixtureは変更なしで合格する。
2. `PRIVATE_RAW`、`RESTRICTED`、秘密、権利不明素材をhandoffへ出さない。
3. 既存の証拠→主張→インサイト→判断→要件→試験の辺を変更しない。
4. production側の状態を`research-state.json`へ混ぜない。
5. 外部送信、購入、契約、公開をテストやCLIが自動実行しない。
6. 生成物を正本として手編集しない。

## Plan of Work

### Milestone H1: Contract and canonical files

#### Goal

空プロジェクト、既存project、handoff projectの三種類を曖昧なく表現できる。

#### Work

1. `schemas/production-hypothesis.schema.json`を追加する。
2. `schemas/prototype-plan.schema.json`を追加する。
3. `schemas/production-handoff.schema.json`を追加する。
4. production result v1 schemaのcommit固定snapshotとprovenance manifestを`schemas/external/`へ追加する。
5. `project-manifest.schema.json`へ任意の`workflow_mode`を追加する。
6. ID、状態、コスト帯、期間帯、選択状態を`config/vocabularies.yaml`へ追加する。
7. `templates/project/`へ仮説、比較、Prototype Plan、handoff、変更要求、取込ログの空雛形を追加する。
8. 正常fixture、フィールド単位異常fixture、既存v1回帰fixtureを追加する。

#### Acceptance

- Draft 2020-12として全schemaが自己検証できる。
- `RESEARCH_ONLY`の既存fixtureはbyte変更なしで合格する。
- `PRODUCTION_HANDOFF` fixtureは新成果物がなければ意図した一規則だけで失敗する。
- schema snapshotはsource repository、commit、取得日時、SHA-256を持つ。

### Milestone H2: Validation and lifecycle

#### Goal

不完全、不正、危険、改変済みのhandoffを外部送信前に拒否する。

#### Work

1. 新schemaを既存schema registryへ接続する。
2. 仮説→判断・インサイト、Prototype→仮説・不確実性・試験、handoff→全正本の参照を検証する。
3. Prototype taskの重複、欠落依存、循環を検査する。
4. 単一仮説理由と複数仮説比較を条件付き検証する。
5. handoff state transition、supersede関係、revisionを検証する。
6. canonical serializationとSHA-256実装を一箇所へ集約する。
7. PRIVATE_RAW、RESTRICTED、秘密、絶対path、署名付きURL、payload上限を検査する。
8. production result schema compatibilityを検査する。
9. named blocking ruleごとに正常fixtureへの単一変異テストを追加する。

#### Acceptance

- 各失敗がfile、fieldまたはline、rule、remediationを返す。
- 同じpayloadは同じhash、意味のある一文字変更は異なるhashになる。
- 非対応schema versionは利用可能版を表示して失敗する。
- `python3 tools/validate.py --check`がexit 0。

### Milestone H3: Handoff generation and export

#### Goal

正本からproductionがそのまま受理できる最小bundleを決定的に生成する。

#### Work

1. `tools/build_handoff.py`を追加し、明示timestampまたは既存metadataから決定的に生成する。
2. 推奨仮説、代替案、要件、試験、制約、gap、trigger、source refsを解決する。
3. `tools/export_handoff.py`を追加し、handoff、対応schema、provenance、manifestをbundle化する。
4. `tools/bundle.py --audience production-agent`を新成果物へ対応させる。
5. `tools/build_graph.py`へPH、PP、HO nodeと辺を追加する。
6. `tools/impact.py`へhandoff起点のupstream/downstreamを追加する。
7. 同一入力でbyte-identicalになるテストを追加する。

#### Acceptance

```text
EV001 → CL001 → IN001 → DC001 → PH001 → PP001
                                  └────→ RQ001 → AT001
PH001 + PP001 + RQ001 + AT001 ─────────→ HO001
```

がgraphとimpact CLIで確認できる。bundleには宣言外ファイル、raw evidence、ローカル絶対pathがない。

### Milestone H4: Production feedback import

#### Goal

制作結果を安全に検証し、二重適用せず、researchの証拠・影響分析へ戻せる。

#### Work

1. `tools/import_production_result.py`へ`--dry-run`と`--apply`を実装する。
2. result schema、source commit、accepted handoff ID/hash、payload hashを検証する。
3. assetを複製せずURI、hash、版、権利区分として登録する。
4. 観察と試験結果をproduction由来のevidence candidateへ変換する。
5. change request、deviation、incidentをgovernance正本へ記録する。
6. result ID/hashをeffect keyとして冪等適用する。
7. `NONE`、`MINOR`、`MAJOR`、`CRITICAL`の影響を既存state machineへ接続する。
8. crash後の再実行と同一ID異内容拒否をテストする。

#### Acceptance

- dry-runはtracked fileを変更しない。
- applyは一度だけevidence candidateと監査イベントを追加する。
- 同じresultの再applyは成功し、内容を増やさない。
- MAJORは明示的reopen event、CRITICALは人間承認待ちとなる。

### Milestone H5: Cross-repo E2E and release

#### Goal

ネットワークなしの固定fixtureでresearch→production→researchを完走し、後方互換性と安全性をrelease gateで保証する。

#### Work

1. `tests/fixtures/harmony-handoff`を追加する。
2. production repoが受理するexpected handoff bundleを固定する。
3. production repoから返るPASS、FAIL、DEVIATION、CRITICAL result fixtureを追加する。
4. source repo停止、schema mismatch、hash改変、重複result、破損JSONLのchaos testを追加する。
5. README、運用、障害対応、schema referenceを更新する。
6. release checkへhandoff、feedback、compatibilityを追加する。
7. CI相当gateを3回連続で実行し、commit SHA付きで記録する。

#### Acceptance

- 既存67件以上のtestと全新規testが合格する。
- 既存`harmony` fixtureの出力互換性を保つ。
- cross-repo fixtureのschema/hashが双方で一致する。
- release gateが3回連続exit 0。
- 外部公開は人間の明示承認後だけ行う。

## Concrete Steps

各タスク開始時に、`execution/task-queue.yaml`で依存完了済みの最小ID `READY` を一件だけ`IN_PROGRESS`へ変更する。各タスク内では次を繰り返す。

```bash
git status --short
python3 -m unittest discover -s tests -v
python3 tools/validate.py --check
python3 tools/build_graph.py
git diff --check
```

生成物差分は原因を確認し、正本変更に由来する場合だけ明示的に含める。既存の無関係なworktree変更をstageしない。

## Validation and Acceptance

全マイルストーン共通で次を満たす。

1. 正常系と失敗系がある。
2. schema、validator、fixture、文書が一致する。
3. 全IDがproject scoped graphで解決する。
4. `RESEARCH_ONLY`回帰が通る。
5. `PRODUCTION_HANDOFF`のblocking ruleが宣言的fixture matrixに載る。
6. 秘密・private境界検査がhandoff exportとfeedback importの両方向にある。
7. 終了時にProgress、Surprises、Decision Log、Outcomes、task queueを更新する。

## Idempotence and Recovery

- schemaと設定追加は既存keyを書き換えず、再適用可能にする。
- generated handoffはtemp fileへ書き、検証後にatomic replaceする。
- export先が同内容なら成功し、異内容なら`--force`なしで上書きしない。
- feedback importはresult IDとpayload hashをeffect keyにする。
- apply途中で停止した場合、監査ログから推測せず正本とeffect recordで再開する。
- external schema取得を通常テストで要求しない。snapshotを使用する。
- rollbackで既存v1成果物やユーザー生成dataを削除しない。

## Interfaces and Dependencies

### Public files

- `schemas/production-hypothesis.schema.json`
- `schemas/prototype-plan.schema.json`
- `schemas/production-handoff.schema.json`
- `schemas/external/production-result.v1.schema.json`
- `projects/<id>/04_decisions/production-hypotheses.yaml`
- `projects/<id>/04_decisions/hypothesis-comparison.yaml`
- `projects/<id>/05_production/prototype-plans.yaml`
- `projects/<id>/05_production/production-handoff.yaml`
- `projects/<id>/06_governance/production-change-requests.yaml`
- `projects/<id>/07_runtime/production-feedback-imports.jsonl`

### Public CLI

- `tools/build_handoff.py`
- `tools/export_handoff.py`
- `tools/import_production_result.py`
- 既存`validate.py`、`build_graph.py`、`bundle.py`、`impact.py`の後方互換拡張

### Dependencies

- Python 3.11以上
- 既存PyYAML、jsonschema
- hashは標準ライブラリ`hashlib`
- canonicalizationは一つの内部moduleへ集約し、複数CLIへ複製しない
- production接続は初期段階でfile bundleのみ。MCP/APIはrelease後の別計画とする

## Task Handoff Template

各タスク終了時に次を更新する。

```text
Task:
Status:
Changed canonical files:
Generated files:
Commands executed:
Results:
New blocking rules:
Surprises:
Remaining risks:
Next READY task:
Exact restart command:
```
