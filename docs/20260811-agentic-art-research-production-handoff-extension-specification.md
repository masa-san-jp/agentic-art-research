# Agentic Art Research 制作仮説・引き渡し拡張設計仕様書

- 作成日: 2026-08-11
- 版: 1.0.0
- 状態: 実装待ち確定仕様
- 対象リポジトリ: `masa-san-jp/agentic-art-research`
- 接続先: `masa-san-jp/agentic-art-production`
- 基礎仕様: `docs/20260811-agentic-art-research-system-design-specification.md`
- 実行計画: `docs/20260811-agentic-art-research-production-handoff-execution-plan.md`

## 0. 結論

本拡張は、既存の証拠から制作要件までの追跡可能性を保ったまま、リサーチの成果を実制作へ安全に渡せる状態へ拡張する。

リサーチと制作は、一つの利用フローとして接続するが、正本、状態機械、変更権限、完了条件を分離する。

```text
agentic-art-research                         agentic-art-production

証拠 → 主張 → インサイト → 制作判断
                           → 制作仮説
                           → 比較・推奨
                           → Prototype Plan
                           → Production Handoff ───────→ 受入・採択
                                                         → 制作計画
                                                         → 試作・本制作
                                                         → Production Result
制作結果を新証拠として正規化 ←───────────────────────────┘
```

本リポジトリの責務は、次で終了する。

> 何を、なぜ、どの仮説として試すべきかを説明でき、根拠、制約、未解決事項、次の試作が機械可読な引き渡しとして固定されていること。

正確な原価、調達、担当、日程、設営、本制作の進捗管理は `agentic-art-production` の責務とする。

## 1. 目的

### 1.1 達成すること

1. 制作判断を、一つ以上の比較可能な制作仮説へ変換する。
2. 各仮説について、根拠、差分、実現可能性、権利、安全性、不確実性を記録する。
3. 採択前に必要な最小試作を、方法と合否条件付きで定義する。
4. 後続制作システムへ渡す情報を、版固定された `production-handoff.yaml` として生成する。
5. 制作側の逸脱、試験結果、観察、変更要求を、新しい証拠候補として還流する。
6. 既存v1プロジェクトを壊さず、段階的に導入する。

### 1.2 成功指標

| 指標 | 合格条件 |
|---|---|
| 仮説追跡性 | 全制作仮説が一つ以上の採用判断を参照する |
| 比較可能性 | 採択候補が複数なら同一評価軸で比較される |
| 単一案の説明可能性 | 仮説が一案だけなら代替案を生成しない理由がある |
| 試作可能性 | 採択前の重大な不確実性にPrototype Planが接続する |
| 引き渡し完全性 | 必須要件、試験、制約、ギャップ、再計画条件が自己完結する |
| 版固定 | handoffがresearch commit、schema version、内容hashを持つ |
| 境界安全性 | 原証拠、PRIVATE_RAW、RESTRICTED、認証情報をhandoffへ含めない |
| 還流可能性 | production resultから証拠候補と影響対象を決定できる |
| 後方互換性 | 既存v1 fixtureが変更なしで検証を通る |

## 2. スコープ

### 2.1 対象

- 制作仮説の生成、比較、推奨、棄却理由
- 既存作品との差分と自己反復リスク
- 技術、概算コスト帯、概算期間帯、場所、権利、安全性の実現可能性評価
- 採択前Prototype Plan
- 制作要件、禁止事項、受入試験、未解決事項の引き渡し
- handoffの版、hash、source commit、supersede関係
- production resultの参照・検証・証拠候補化
- 変更影響に基づくリサーチ再開

### 2.2 スコープ外

- 正確な見積、発注、購入、契約
- 担当者へのアサイン、工数実績、勤怠
- 詳細工程表、搬入、設営、撤去の実行管理
- 完成作品、動画、音声、3D、制作データ本体のGit保存
- 制作側のタスク状態を本リポジトリの状態機械で管理すること
- 制作結果を根拠なしに採用判断へ自動昇格すること

## 3. 責任分界

| 情報 | 正本 | 相手側での扱い |
|---|---|---|
| 原証拠、主張、インサイト | research | ID、要約、commitを参照する |
| 制作判断、棄却案 | research | handoff snapshotを読み取り専用で使う |
| 制作仮説、Prototype Plan | research | 採択判断と制作計画の入力にする |
| 制作要件、禁止事項、受入試験 | research | 基準線として固定し、変更要求でのみ改訂する |
| 採択、スコープ基準線 | production | selection resultだけをresearchへ返す |
| WBS、日程、予算、BOM、資源 | production | researchは結果と変更影響だけを参照する |
| 試作・制作・展示の観察結果 | production | URI、hash、要約をresearchで証拠候補化する |
| 完成作品本体 | asset store / production | 両Gitには参照情報だけを保存する |

制作側はresearch要件を黙って解釈変更してはならない。実現不能、矛盾、予算超過、会場変更を発見した場合は、理由と代替案を持つ変更要求を返す。

research側は制作結果を自動で「事実」または「採用判断」にしてはならない。出所、実施条件、結果、限界を検証してから既存のevidence/claim/decisionフローへ投入する。

## 4. 互換性方針

### 4.1 導入モード

プロジェクトmanifestへ任意の `workflow_mode` を追加する。

- 未指定または `RESEARCH_ONLY`: 既存v1と同じ。handoff成果物は不要。
- `PRODUCTION_HANDOFF`: 本仕様の仮説、Prototype Plan、handoffを必須にする。

未指定値はvalidator内部で `RESEARCH_ONLY` と解釈する。既存ファイルを書き換えない。

### 4.2 版管理

- 既存プロジェクトに対する任意追加として実装できる限り、リポジトリ版はMINOR更新とする。
- 既存必須フィールド、ID意味、状態遷移を破る場合はMAJOR更新とする。
- handoff schemaは独立したSemVerを持つ。
- 公開済みschema versionの意味を変更しない。修正は新しい版として追加する。

## 5. 新しい成果物

```text
projects/<project-id>/
├── 04_decisions/
│   ├── decision-log.yaml
│   ├── production-hypotheses.yaml        # 新規正本
│   └── hypothesis-comparison.yaml        # 新規正本
├── 05_production/
│   ├── creative-direction.md
│   ├── production-requirements.yaml
│   ├── acceptance-tests.yaml
│   ├── prototype-plans.yaml              # 既存空雛形を正式化
│   ├── production-handoff.yaml           # 新規正本
│   └── production-agent-context.md       # 生成物
├── 06_governance/
│   └── production-change-requests.yaml   # 新規正本
└── 07_runtime/
    └── production-feedback-imports.jsonl # 取込監査ログ
```

`data/` にはhandoff bundle、互換性レポート、影響分析などの再生成可能な出力だけを置く。

## 6. 制作仮説

### 6.1 構造

```yaml
hypotheses:
  - id: PH001
    title: 反復間隔の中断を身体的に知覚させる小規模インスタレーション
    proposition: 規則的な反復の一箇所だけを欠落させ、観客の移動で中断を発見させる
    source_decision_ids: [DC001]
    source_insight_ids: [IN001]
    intended_experience:
      - 最初は秩序として認識し、移動後に一箇所の断絶へ気づく
    includes:
      - 反復する立体要素
      - 一箇所の欠落
    excludes:
      - 説明文による中断位置の明示
    differentiation:
      precedent_refs: [XR001]
      statement: 音響ではなく移動視差によって欠落を発見させる
    feasibility:
      technical: PLAまたは紙模型で検証可能
      cost_band: LOW
      duration_band: DAYS
      venue_dependency: MEDIUM
      rights_status: CLEAR
      safety_status: REVIEW_REQUIRED
    uncertainties:
      - id: U001
        statement: 三方向から中断が知覚できるか
        severity: MAJOR
        prototype_plan_ids: [PP001]
    recommendation: RECOMMENDED
    status: ADOPTED
```

### 6.2 生成数

- 一つ以上の仮説を必須とする。
- 実質的な選択肢が存在する場合は二つ以上を比較する。
- 一案だけの場合は `single_hypothesis_rationale` を必須とする。
- 数を満たすためだけの弱い案を生成してはならない。
- 中心命題を別作品へ変える仮説は、人間承認なしに採用しない。

### 6.3 比較軸

候補が複数の場合、最低限次を同じ尺度で比較する。

- 制作意図との整合
- 証拠と判断への追跡可能性
- 先行作品との差分
- 制作者本人の過去作品との差分
- 観客経験の明確さ
- 技術的実現可能性
- コスト帯と期間帯
- 権利、安全、プライバシー
- 試作による検証可能性
- 重大な未解決事項

数値スコアは並べ替えにだけ使用し、推奨理由を代替しない。

## 7. Prototype Plan

Prototype Planは、完成工程ではなく、制作仮説の重大な不確実性を最小コストで減らす実験である。

```yaml
prototype_plans:
  - id: PP001
    hypothesis_id: PH001
    question: 三方向から反復の中断を知覚できるか
    uncertainty_ids: [U001]
    method: 1/10模型を固定照明下で三方向から観察する
    inputs:
      - 紙模型12要素
      - 固定カメラ3台
    constraints:
      - 人物を撮影しない
    tasks:
      - id: PT001
        title: 模型を組み立てる
        depends_on: []
        completion_condition: 12要素中1要素を欠落させた模型が固定されている
      - id: PT002
        title: 三方向から記録する
        depends_on: [PT001]
        completion_condition: 同一露出の静止画が3枚ある
    acceptance_test_ids: [AT001]
    expected_evidence: fixed-camera-frame-set
    estimated_cost_band: LOW
    estimated_duration_band: HOURS
    executor_capability: physical-prototype-agent
    status: PLANNED
```

research側では、担当者名、購入先、正確な価格、確定日時を必須にしない。それらは採択後にproduction側で基準線化する。

## 8. Production Handoff契約

### 8.1 正本と所有権

- handoff request schemaの正本は本リポジトリとする。
- production側は対応版のimmutable snapshotを保存し、元schemaのcommit SHAを記録する。
- production result schemaの正本はproductionリポジトリとする。
- research側は対応版のimmutable snapshotを保存し、同じ互換性検査を行う。

### 8.2 必須構造

```yaml
schema_version: 1.0.0
handoff_id: HO001
research_project_id: project/harmony-study
research_project_version: 1.1.0
research_commit: "<40-character-git-sha>"
generated_at: 2026-08-11T21:00:00+09:00

selection:
  status: AGENT_RECOMMENDED
  selected_hypothesis_id: PH001
  alternative_hypothesis_ids: [PH002]
  authority: agent-recommended
  human_approval_required: false

creative_direction_ref: projects/harmony-study/05_production/creative-direction.md

requirements:
  - id: RQ001
    statement: 反復する間隔と一度の中断を観察可能にする
    priority: mandatory
    source_decision_ids: [DC001]
    acceptance_test_ids: [AT001]

prototype_plan_ids: [PP001]

constraints:
  rights: []
  safety:
    - 素材変更時に安全性を再確認する
  privacy: []
  prohibited_actions:
    - 人間承認なしに購入、契約、公開を行わない

open_gaps:
  - id: GP001
    statement: 実会場寸法が未確定
    impact: サイズと搬入経路を確定できない
    resolution_owner: production

replan_triggers:
  - 会場変更
  - 素材仕様変更
  - 必須受入試験の不合格

source_refs:
  decision_ids: [DC001]
  insight_ids: [IN001]
  evidence_ids: [EV001, EV002]

integrity:
  content_sha256: "<sha256-of-canonical-payload>"
```

### 8.3 含めてよい情報

- 採用または候補となる制作仮説
- クリエイティブ・ディレクション
- 制作要件、禁止事項、受入試験
- Prototype Plan
- 権利、安全、プライバシー制約
- 未解決事項、仮定、再計画条件
- source ID、source commit、公開可能な短い要約

### 8.4 含めてはならない情報

- `PRIVATE_RAW`、`RESTRICTED`
- 個人メール、カレンダー詳細、非公開音声、個人メモ原文
- 認証情報、署名付きURL、ローカル絶対パス
- 権利不明素材の複製
- handoffの判断に不要な全文証拠
- 未検証の推論を確定事項として表現した値

## 9. Handoffライフサイクル

research本体の状態機械とは別に、handoff自身の状態を持つ。

```text
DRAFT
  → READY
  → ACCEPTED
  → SUPERSEDED

DRAFT / READY → CANCELLED
READY → REJECTED
```

| 状態 | 条件 |
|---|---|
| `DRAFT` | 仮説または必須項目が未確定 |
| `READY` | schema、参照、権利、機密性、hashが検証済み |
| `ACCEPTED` | production側が対応版と受領記録を返した |
| `REJECTED` | 非互換またはblocking gapと解除条件が返された |
| `SUPERSEDED` | 新handoff IDが後継として明記された |
| `CANCELLED` | 人間が中止し、理由と保持方針がある |

既存handoffを上書きして意味を変えない。内容変更時は新しいhandoff IDまたはrevisionを生成し、`supersedes` を記録する。

## 10. Production Result還流契約

production側から最低限次を受け取る。

- production project ID、production commit、result schema version
- accepted handoff IDとhash
- 採択結果と採択権限
- Prototype/Acceptance Testの実施条件と結果
- 実制作で観察された事実と限界
- 要件からの逸脱、その理由、承認者
- 安全、権利、プライバシー上のインシデント
- 成果物のURI、版、hash、権利区分
- researchへの変更要求、新しい証拠候補、再調査質問候補

取込手順は固定する。

1. schema version、source commit、hashを検証する。
2. raw assetや禁止情報が埋め込まれていないことを検査する。
3. resultを不透明URIとhash付きの外部参照として台帳化する。
4. 観察を既存の証拠型へ正規化し、production result IDを出所にする。
5. 既存主張、判断、要件への影響を算出する。
6. `NONE` / `MINOR` は記録・局所更新する。
7. `MAJOR` は `ANALYZING` へ再開する。
8. `CRITICAL` は制作停止要求と人間承認へ送る。

## 11. 検証規則

`workflow_mode: PRODUCTION_HANDOFF` のとき、次をblocking ruleとする。

1. 制作仮説が一つ以上ある。
2. 全仮説が採用判断を参照する。
3. 複数候補は共通軸で比較済み。
4. 単一候補は理由を持つ。
5. `MAJOR`以上の不確実性はPrototype Planまたは外部検証理由を持つ。
6. Prototype Planの全タスク依存が解決し、循環しない。
7. 必須要件がhandoffに欠落していない。
8. 必須要件の受入試験がhandoffに存在する。
9. source decision、insight、evidence IDがresearch project内で解決する。
10. handoffのsource commitが40桁SHAで固定される。
11. canonical payloadから再計算したSHA-256が一致する。
12. 禁止区分、秘密、絶対パス、未許可原文を含まない。
13. `HUMAN_SELECTION_REQUIRED` で採択済みと偽らない。
14. `READY` handoffにblocking gapがない。
15. supersede関係が循環しない。

既存 `RESEARCH_ONLY` プロジェクトには1〜15を適用しない。

## 12. 生成CLI

実装後の公開インターフェースを次に固定する。

```bash
python3 tools/build_handoff.py project/harmony-study
python3 tools/validate.py --project harmony-study --check
python3 tools/export_handoff.py project/harmony-study --output data/handoffs/harmony-study
python3 tools/import_production_result.py path/to/production-result.yaml --dry-run
python3 tools/import_production_result.py path/to/production-result.yaml --apply
python3 tools/impact.py --production-result PR001
```

- `build_handoff.py` は正本から決定的にhandoffを生成する。
- `export_handoff.py` はhandoff、schema、必要な公開可能参照だけを含むbundleを生成する。
- `import_production_result.py --dry-run` は変更せず検証・影響だけを表示する。
- `--apply` は検証済み結果だけを台帳、証拠候補、監査ログへ追記する。
- 同じresult IDとhashの再取込は冪等に成功し、異なる内容で同じIDなら失敗する。

## 13. エージェント権限

### 13.1 自律実行してよいこと

- 制作仮説候補と比較の生成
- Prototype Plan候補の生成
- 定義済みschemaによるhandoff生成・検証
- production resultのdry-run、重複検知、影響分析
- `COMPLETE_WITH_GAPS` または `HUMAN_SELECTION_REQUIRED` での停止

### 13.2 人間承認が必要なこと

- 中心命題を別作品とみなす変更
- 同等候補から作者の最終意思を確定すること
- 公開、応募、購入、契約、削除、外部送信
- 権利、安全、第三者プライバシーに重大な不確実性がある採択
- production resultが示すCRITICALな変更を採用判断へ反映すること

## 14. 完了条件

`PRODUCTION_HANDOFF` モードのresearch projectは、既存完了条件に加え次を満たす。

- 制作仮説と比較がschema-valid。
- 推奨案または `HUMAN_SELECTION_REQUIRED` が明示されている。
- 重大な不確実性にPrototype Planまたは外部検証理由がある。
- `production-handoff.yaml` が `READY`。
- handoffから必須要件、受入試験、判断、インサイト、証拠まで逆参照できる。
- handoff bundleに禁止情報がない。
- production側が利用できるschema versionと互換性が確認されている、または未確認理由がgapとして残る。

production側で作品が完成していなくてもresearch projectは完了できる。productionの進捗をresearch完了の条件にしてはならない。

## 15. 実装フェーズ

詳細は対応する実行計画を正本とする。

1. **H1 Contract** — 新schema、語彙、正常・異常fixture
2. **H2 Validation** — 参照、DAG、機密性、hash、互換性
3. **H3 Handoff** — 仮説比較、handoff生成、export、bundle
4. **H4 Feedback** — production result取込、冪等性、影響分析、再開
5. **H5 E2E** — 両repo fixture、障害試験、文書、release gate

## 16. 受入シナリオ

### 16.1 正常系

1. `harmony-study` を `PRODUCTION_HANDOFF` モードにする。
2. 二つの制作仮説を同じ軸で比較する。
3. PH001を推奨し、PP001で重大な不確実性を検証可能にする。
4. HO001を決定的に生成する。
5. production fixtureがHO001を受理し、PR001を返す。
6. PR001の試験結果をresearchへdry-runして影響を確認する。
7. apply後、production由来証拠と監査ログが一度だけ追加される。
8. EV001からPR001まで、PR001から影響要件までを辿れる。

### 16.2 失敗系

- 必須要件欠落handoffを拒否する。
- hash不一致を拒否する。
- PRIVATE_RAWを含むhandoffを拒否する。
- 未承認の人間選択を採択済みにしたhandoffを拒否する。
- Prototype task循環を拒否する。
- 同じresult IDで異なる内容を拒否する。
- 非対応schema versionを明確なremediation付きで拒否する。
- CRITICAL resultを自動採用せず、人間承認へ送る。

## 17. 非機能要件

- Python 3.11以上、初期依存は既存範囲を優先する。
- 同じ正本とtimestamp入力からbyte-identicalなhandoffを生成する。
- エラーはファイル、field、rule、remediationを含む。
- 外部repo停止中もfixtureで全検証を実行できる。
- production repoの書込権限をresearch runtimeへ常時与えない。
- GitHub、MCP、クラウドストレージはadapter境界の後ろへ置く。
- 全新機能に正常系、失敗系、再実行系テストを追加する。

## 18. 設計決定

| 論点 | 決定 | 理由 |
|---|---|---|
| 統合か分離か | パイプラインは統合、正本と状態機械は分離 | 追跡可能性と独立した実行周期を両立するため |
| research終点 | 制作仮説、Prototype Plan、handoff | 「何を作るべきか」を制作側が再推論しないため |
| production終点 | 制作・設営・結果還流 | 実行上の事実をresearchの推測から分離するため |
| 仮説数 | 一つ以上、複数候補があるとき比較 | 形式的な水増し案を避けるため |
| 完全な証拠複製 | 禁止 | 機密性、権利、正本競合を防ぐため |
| schema所有 | requestはresearch、resultはproduction | 生成者が契約の意味を正本化するため |
| schema共有 | commit固定immutable snapshot | オフライン検証と供給元追跡を両立するため |
| feedback | 新証拠候補として取込 | 制作結果で過去判断を黙って上書きしないため |
| 既存v1 | `RESEARCH_ONLY` として互換維持 | 公開済み利用契約を壊さないため |

## 19. 未決事項の扱い

次は実装時に推測で固定せず、fixtureと計測結果をDecision Logへ記録して決める。

- canonical YAML hashの具体的正規化方式
- schema snapshot自動更新のCLI形状
- 大きなproduction resultに対する最大payloadサイズ
- 外部asset URIの許可scheme一覧
- production側との互換性matrixの保存形式

これらは境界契約の意味を変更しない実装詳細に限る。責任分界、禁止情報、人間承認条件は変更してはならない。
