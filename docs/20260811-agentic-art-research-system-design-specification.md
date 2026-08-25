# Agentic Art Research システム設計仕様書

- 作成日: 2026-08-11
- 版: 1.0.0
- 状態: 確定仕様
- 対象リポジトリ: `masa-san-jp/agentic-art-research`
- 要求トラッキング: GitHub Issue #1
- 既存仕様の扱い: `docs/spec-v0.md` は本書の「成果物・利用仕様」の原案として統合する

## 0. 結論

本システムは、AIエージェントがアート制作に必要な調査を自律的に計画・実行し、原証拠から制作要件までを追跡可能な形で出力するためのリサーチ基盤である。

単一の長文レポートを作ることを目的としない。次の連鎖を、相互参照可能な別々の成果物として保存する。

```text
制作意図
  → 調査質問
  → 証拠
  → 観察・主張
  → インサイト
  → 制作判断
  → 制作要件
  → 試作・受入試験
  → 完成後の学習
```

完了状態は次のとおりとする。

- 人間は、要点・判断理由・未解決事項・次の制作行動を短い文書で確認できる。
- 後続エージェントは、必要な証拠・制約・制作要件だけを機械的に取得できる。
- 判断と制作要件は、元の証拠まで逆方向に辿れる。
- 未確認事項、反証、棄却案を消さずに保持する。
- 新しい証拠が追加されたとき、影響する主張・判断・要件だけを再計算できる。
- 個人メール、カレンダー、非公開メモ等の生データをGitへ保存しない。

## 1. 目的

### 1.1 達成すること

1. 曖昧な制作テーマを、有限個の調査質問と完了条件へ変換する。
2. 外部資料と制作者本人のデータを、出所・権利・機密性付きの証拠として扱う。
3. 事実、他者の主張、観察、解釈、推論、本人の嗜好シグナルを区別する。
4. 調査結果を、制作へ直接投入できる要件・禁止事項・試作・受入試験へ変換する。
5. 制作中と完成後の結果を、次回のリサーチ資産へ還流する。
6. 1件のリサーチを、単独のAIエージェントが固定手順で終えられるようにする。

### 1.2 成功指標

件数ではなく、利用可能性で評価する。

| 指標 | 合格条件 |
|---|---|
| 追跡可能性 | 採用された全制作要件が、判断、インサイト、主張、証拠へ遡れる |
| 完結性 | 必須質問が回答済み、または根拠付きで `UNRESOLVED` になっている |
| 実行可能性 | 必須制作要件に検証可能な受入試験がある |
| 再利用性 | プロジェクト固有情報と再利用可能な知識が分離されている |
| 監査可能性 | 出所、取得日時、権利、機密性、変換履歴が記録されている |
| 自律性 | 通常ケースでは、事前方針の範囲内で人間への都度質問なしに完了できる |
| 安全性 | `PRIVATE_RAW` がGit履歴へ混入せず、公開可否が未判定の素材を外部出力しない |

## 2. スコープ

### 2.1 対象

- 制作テーマの明確化
- 調査質問の生成と優先順位付け
- 公開情報、所蔵館資料、論文、カタログ、アーカイブの調査
- 制作者本人の許可済みメモ、過去作品、文章、予定、会話記録等の分析
- 先行作品、類似表現、技術、素材、場所、権利、安全性の調査
- 証拠・主張・反証・不確実性の構造化
- 美意識、関心、回避傾向、反復傾向の仮説化
- 制作判断、制作要件、試作バックログ、受入試験の生成
- 制作中レビューと完成後レビュー
- 差分更新、変更影響分析、再利用

### 2.2 スコープ外

- 本リポジトリ内での作品ファイル本体の恒久保管
- 個人メール、カレンダー、非公開音声等の生データのGit保存
- 根拠のない作者意図、美意識、歴史的因果の断定
- AIが物理的な現地観察、実物鑑賞、観客反応を行ったと偽ること
- 権利不明な画像・文書・音声の再配布
- 自動購入、契約、応募、公開、外部送信
- `art-history-notes` が正本とする一般美術史データの重複保有
- 初期実装における常時稼働DB、Webアプリ、独自ベクトルDBの必須化

## 3. 設計原則

1. **原証拠・解釈・判断・制作要件を分離する。**
2. **正本と生成物を分離する。** 人が編集する正本から、グラフ、索引、被覆表、バンドルを生成する。
3. **未確認を推測で埋めない。** 不明は `null`、未調査は `NOT_RESEARCHED`、調査したが確定不能は `UNRESOLVED` とする。
4. **主張ごとに根拠を持つ。** ファイル末尾の参考文献一覧だけで済ませない。
5. **棄却案と反証を保存する。** 採用案だけでは判断を再現できない。
6. **エージェントに設計判断を残さない。** 語彙、閾値、遷移条件、検証規則を設定とツールへ寄せる。
7. **1タスク1成果単位にする。** 1回で複数の大きな調査対象を仕上げない。
8. **数値スコアだけで断定しない。** スコアは並べ替え用であり、根拠・反証・適用範囲を必ず併記する。
9. **類似は因果ではない。** 意味的・形式的類似から影響関係を自動生成しない。
10. **個人データは最小限に派生化する。** 原文を渡さず、目的に必要なシグナルだけを後続工程へ渡す。
11. **外部知識は参照する。** 一般美術史は `art-history-notes` の安定ID・出典付きバンドルを優先する。
12. **完了できる有限プロセスにする。** 検索回数、再試行回数、予算、終了状態を開始時に固定する。

## 4. システム境界

### 4.1 他リポジトリとの責任分界

| 領域 | 正本 | 本リポジトリでの扱い |
|---|---|---|
| 一般美術史、運動、作家、作品、場所、時代文脈 | `art-history-notes` | URN、commit SHA、取得日時、必要な抜粋を参照する |
| 制作者本人の美意識・関心の派生情報 | 外部profile output root | `profiles/` はREADMEのみを置き、`--profiles-root`で根拠ID・有効期限付きの派生signalを検証する |
| 作品ごとの調査、判断、制作要件 | 本リポジトリ | `projects/<project-id>/` を正本とする |
| 私的な原データ | Google Drive、Personal Data Fabric等の許可済み保管先 | URI、ハッシュ、メタデータのみを台帳に保存する |
| 完成作品本体 | 制作リポジトリまたはアセット保管先 | 作品ID、版、ハッシュ、参照URIのみを保存する |

`art-history-notes` の情報を取り込む場合、コピーを正本にしない。次を記録する。

```yaml
external_reference:
  id: XR001
  system: art-history-notes
  entity_id: movement/surrealism
  uri: urn:ahn:movement/surrealism
  source_commit: "<git-sha>"
  acquired_at: 2026-08-11T15:00:00+09:00
  usage: precedent_research
```

### 4.2 原データの保存境界

| 区分 | Git保存 | 例 |
|---|---:|---|
| `PRIVATE_RAW` | 禁止 | メール本文、カレンダー詳細、非公開音声、個人メモ原文 |
| `PRIVATE_DERIVED` | 条件付き | 美意識シグナル、関心推定。原文復元が困難な最小限の派生情報 |
| `PROJECT_INTERNAL` | 可 | プロジェクト内の判断、非公開制作要件 |
| `PUBLIC_CITABLE` | 可 | 公開URL、引用可能な出典、公開用要約 |
| `RESTRICTED` | 禁止 | 同意なしの第三者情報、契約・権利上保存できない内容 |

Gitへ保存しない証拠も、`evidence-ledger` には存在を記録する。ただし `source_location` は許可されたエージェントだけが解決できるURIとし、認証情報を含めない。

## 5. 利用者と機能ロール

ロールは固定人格ではなく機能である。小規模運用では1体のエージェントが複数ロールを順番に実行してよい。

| ロール | 責務 | 禁止事項 |
|---|---|---|
| Orchestrator | 状態管理、タスク分解、予算・終了判定 | 証拠なしに結論を確定しない |
| Planner | 制作意図を質問・調査計画へ変換 | 無限に質問を増やさない |
| Collector | 許可済みソースから資料取得 | アクセス権限を迂回しない |
| Curator | 重複排除、メタデータ、権利、機密性の付与 | 原文を勝手に公開区分へ上げない |
| Analyst | 観察、主張、関係、反証を抽出 | 事実と解釈を混同しない |
| Critic | 反証、代替解釈、自己反復、既視感を検査 | スコアだけで棄却しない |
| Decision Translator | インサイトを制作判断へ変換 | 作者の最終意思を捏造しない |
| Production Translator | 判断を制作要件と受入試験へ変換 | 検証不能な要件を必須にしない |
| Validator | スキーマ、参照、権利、状態遷移を検査 | エラーを黙って補正しない |
| Auditor | 変更影響、偏り、未解決事項を報告 | 正本や生成物を手作業で改変しない |

## 6. 権限と人間へのエスカレーション

### 6.1 自律実行してよいこと

- 許可済みの公開情報と接続済みデータの読み取り
- 調査計画、検索、要約、主張抽出、反証探索
- 既定スキーマ内でのファイル作成・更新
- 制作案、判断候補、要件候補、試作候補の生成
- 機械検証、差分分析、失敗時の規定回数内の再試行
- `COMPLETE_WITH_GAPS` として未解決を残して終了すること

### 6.2 人間の承認が必要なこと

- 新しい個人データ領域へのアクセス許可
- 公開、応募、外部送信、購入、契約、削除
- `PRIVATE_DERIVED` を `PROJECT_INTERNAL` または `PUBLIC_CITABLE` へ変更すること
- 肖像権、著作権、身体安全、第三者のプライバシーに重大な不確実性がある制作判断
- 中心命題を別作品とみなすほど変更すること
- 既定の予算、期限、取得範囲を超えること

### 6.3 質問せず終了する条件

次の場合は人間へ都度質問せず、理由を記録して終了する。

- 調査しても資料が見つからない: `COMPLETE_WITH_GAPS`
- ソースへアクセスできない: `BLOCKED_SOURCE_ACCESS`
- 権利が確認できない素材: 採用しないで `REJECTED_RIGHTS_UNKNOWN`
- 判断に必要な物理実験ができない: 代替試作を提示し `EXTERNAL_VALIDATION_REQUIRED`
- 複数案が同等: 推奨案と差分を示し `HUMAN_SELECTION_REQUIRED`

## 7. リポジトリ構造

```text
agentic-art-research/
├── README.md
├── config/
│   ├── vocabularies.yaml
│   ├── confidence-rubric.yaml
│   ├── stopping-policy.yaml
│   ├── access-policy.yaml
│   └── retention-policy.yaml
├── schemas/
│   ├── project-manifest.schema.json
│   ├── evidence.schema.json
│   ├── claim.schema.json
│   ├── observation.schema.json
│   ├── relationship.schema.json
│   ├── contradiction.schema.json
│   ├── external-reference.schema.json
│   ├── aesthetic-signal.schema.json
│   ├── insight.schema.json
│   ├── decision.schema.json
│   ├── requirement.schema.json
│   ├── research-plan.schema.json
│   ├── prior-art.schema.json
│   ├── self-repetition-review.schema.json
│   └── completion-report.schema.json
├── docs/
│   ├── 20260811-agentic-art-research-system-design-specification.md
│   ├── research-task-protocol.md
│   ├── schema-reference.md
│   ├── governance.md
│   └── integration-art-history-notes.md
├── templates/
│   └── project/
├── profiles/                         # protocol README only; instances are external
├── projects/
│   └── <project-id>/
│       ├── manifest.yaml
│       ├── 00_intake/
│       ├── 01_planning/
│       ├── 02_evidence/
│       ├── 03_knowledge/
│       ├── 04_decisions/
│       ├── 05_production/
│       ├── 06_governance/
│       └── 07_runtime/
├── tools/
│   ├── new_project.py
│   ├── validate.py
│   ├── build_graph.py
│   ├── bundle.py
│   ├── impact.py
│   └── audit.py
├── tests/
├── data/                       # 生成物。手編集禁止
└── .github/workflows/validate.yml
```

### 7.1 プロジェクト標準構造

```text
projects/<project-id>/
├── manifest.yaml
├── 00_intake/
│   ├── creative-intent.md
│   └── constraints.yaml
├── 01_planning/
│   ├── question-register.yaml
│   ├── research-plan.yaml
│   └── task-queue.jsonl
├── 02_evidence/
│   ├── evidence-ledger.jsonl
│   ├── source-ledger.jsonl
│   └── approved-snapshots/
├── 03_knowledge/
│   ├── observations.jsonl
│   ├── claims.jsonl
│   ├── relationships.jsonl
│   ├── contradictions.jsonl
│   └── external-references.jsonl
├── 04_decisions/
│   ├── insight-register.yaml
│   ├── decision-log.yaml
│   ├── rejected-options.yaml
│   └── uncertainty-register.yaml
├── 05_production/
│   ├── creative-direction.md
│   ├── production-requirements.yaml
│   ├── visual-language.yaml
│   ├── acceptance-tests.yaml
│   ├── prototype-backlog.yaml
│   └── production-agent-context.md
├── 06_governance/
│   ├── rights-register.yaml
│   ├── privacy-review.yaml
│   ├── ethics-review.md
│   └── safety-risk-register.yaml
└── 07_runtime/
    ├── research-state.json
    ├── run-log.jsonl
    ├── change-log.jsonl
    ├── dependency-index.json
    └── completion-report.json
```

`approved-snapshots/` に保存できるのは、ライセンスと機密区分がGit保存を許可する資料だけとする。その他は台帳の参照URIとハッシュだけを持つ。

## 8. ライフサイクル

### 8.1 状態

| 状態 | 意味 | 次へ進む条件 |
|---|---|---|
| `DRAFT` | 制作意図の入力中 | 目的、期限、利用先が記録済み |
| `PLANNED` | 質問と終了条件が確定 | 必須質問、予算、ソース範囲、禁止事項が検証済み |
| `COLLECTING` | 証拠収集中 | 必須質問ごとの探索が終了条件へ到達 |
| `NORMALIZING` | 証拠を台帳化 | 全取得物に出所、権利、機密性、ハッシュがある |
| `ANALYZING` | 主張・反証・関係を抽出 | 必須質問に回答候補またはギャップがある |
| `DECIDING` | インサイトと制作判断を生成 | 採用・棄却・保留と理由が記録済み |
| `READY_FOR_PRODUCTION` | 制作へ渡せる | 必須要件と受入試験が参照解決済み |
| `VALIDATING` | 試作・権利・安全性を検証 | 合格、要修正、外部検証必要のいずれかに確定 |
| `COMPLETE` | 必須項目をすべて満たした | 完了報告がスキーマ適合 |
| `COMPLETE_WITH_GAPS` | 利用可能だが外部ギャップが残る | ギャップ、影響、再開条件が明記済み |
| `BLOCKED` | 権限・安全・外部依存で停止 | ブロッカーと必要な解除条件が明記済み |
| `CANCELLED` | 中止 | 中止理由と保存方針が記録済み |

状態遷移は `research-state.json` のみを正本とし、ログから推測しない。不正な逆行はvalidatorが拒否する。新証拠による再開は `COMPLETE* → ANALYZING` の明示的な再開イベントとして記録する。

### 8.2 1件の標準実行手順

順番を変えない。

1. **受付** — 制作意図、利用先、期限、制約、公開範囲を記録する。
2. **重複確認** — 既存プロジェクト、過去作品、`art-history-notes`、既取得ソースを検索する。
3. **計画** — 必須質問、任意質問、必要証拠、探索戦略、予算、終了条件を固定する。
4. **個人証拠収集** — 許可済み範囲から関連候補のみを取得し、最小限の派生シグナルへ変換する。
5. **外部証拠収集** — 一次情報を優先し、独立した出典と反対証拠を探す。
6. **正規化** — 証拠ID、ハッシュ、出所、権利、機密性、取得日時を付ける。
7. **知識化** — 観察、主張、解釈、推論、反証、関係を型付きで保存する。
8. **分析** — 美意識、先行例、自己反復、新規性、実現性、権利、安全性を比較する。
9. **判断** — 採用、棄却、保留、再検証条件を記録する。
10. **制作変換** — クリエイティブ・ディレクション、要件、禁止事項、試作、受入試験へ変換する。
11. **検証** — スキーマ、参照、権利、機密性、状態遷移、生成物の鮮度を機械検証する。
12. **バンドル** — 人間用要約と後続エージェント用コンテキストを生成する。
13. **終了** — `COMPLETE`、`COMPLETE_WITH_GAPS`、`BLOCKED` のいずれかを必ず返す。

## 9. 調査計画と停止規則

### 9.1 質問オブジェクト

```yaml
question:
  id: Q001
  text: この主題を欠落によって表現した先行作品は何か
  type: precedent
  priority: mandatory
  decision_target: DC001
  required_evidence:
    preferred: primary
    minimum_independent_sources: 2
  status: OPEN
  stop_condition:
    sufficient_answers: 5
    max_search_strategies: 3
    max_sources_reviewed: 30
  reopen_triggers:
    - new_candidate_work
    - creator_rejects_current_options
```

### 9.2 既定停止規則

値は `config/stopping-policy.yaml` で変更可能とし、プロジェクト開始時に固定する。

- 1質問につき検索戦略は最大3系統。
- 同じアクセス失敗の再試行は最大2回。
- 必須質問ごとの確認資料は原則最大30件。
- 新規の有効証拠が連続2ラウンドで得られなければ飽和と判定する。
- 一次資料が存在しない場合、独立した二次資料2件以上を目標にし、一次資料不在を明記する。
- 重大な反証がある場合、単一結論にせず `CONTESTED` とする。
- 上限到達後も結論が出ない場合、質問を `UNRESOLVED` にして全体を `COMPLETE_WITH_GAPS` で終えてよい。

停止規則は「真実が確定した」ことを意味しない。「現在の制約内で、追加探索の期待価値が低い」ことを意味する。

## 10. 証拠モデル

### 10.1 証拠

```yaml
evidence:
  id: EV001
  source_type: personal_note
  source_location: gdrive://<opaque-id>
  creator: <creator-id>
  created_at: unknown
  acquired_at: 2026-08-11T15:00:00+09:00
  content_hash: sha256:<hash>
  rights_status: private-use-only
  sensitivity: PRIVATE_RAW
  redistribution: prohibited
  related_projects: [project/example]
  related_questions: [Q001]
  extraction_status: processed
  observed_by: agent
  direct_observation: false
```

### 10.2 証拠の規律

- URLだけでなく、取得日時と必要に応じてスナップショット識別子を持つ。
- 引用は必要最小限にし、権利上許可される範囲を超えて保存しない。
- 実物未見の場合は `direct_observation: false` を必須とする。
- AIが生成した要約、画像、推論は一次証拠として扱わない。
- 同一内容を転載した複数ページは独立資料として数えない。
- 本人データから得た嗜好は `PREFERENCE_SIGNAL` であり、恒久的な人格事実ではない。
- 第三者情報を含む個人証拠は、派生時に匿名化または不要部分を除去する。

### 10.3 AIが一次証拠を生成できない場合

次の代替手段を順に使う。

1. 許可済みの本人メモ、過去作品、文章、会話、制作ログを参照する。
2. 所蔵館、作者、主催者、法令、規格等の一次資料を取得する。
3. 遠隔で確認可能な公開映像、図面、画像、データを観察資料として使う。
4. 実物の代わりに試作、シミュレーション、合成データで仮説を検査する。
5. 代替不能なら `EXTERNAL_VALIDATION_REQUIRED` とし、必要な人間行動と判定方法を出力する。

代替資料を実地観察、実物鑑賞、観客テストとして記録してはならない。

## 11. 主張・推論・信頼度

### 11.1 主張型

| 型 | 意味 |
|---|---|
| `FACT` | 一次資料等で直接確認できる事実 |
| `SOURCE_CLAIM` | 出典の話者・著者が述べている内容 |
| `OBSERVATION` | 画像、作品、記録から観察された特徴 |
| `INTERPRETATION` | 証拠の意味づけ |
| `INFERENCE` | 複数証拠から導いた推論 |
| `PREFERENCE_SIGNAL` | 本人の関心・美意識を示す時点付きシグナル |
| `HYPOTHESIS` | 未検証の説明候補 |

```yaml
claim:
  id: CL001
  statement: 直接説明より、欠落によって主題を示す形式が反復されている
  type: INFERENCE
  evidence_ids: [EV003, EV007, EV021]
  supporting_claims: []
  opposing_claims: [CL034]
  scope: creator/project-history
  epistemic_status: SUPPORTED
  confidence:
    source_reliability: 3
    directness: 2
    corroboration: 3
    freshness: 4
    scope_fit: 3
    conflict_penalty: 1
    derived_score: 69
  valid_from: 2026-08-11
  review_after: 2027-02-11
```

### 11.2 信頼度計算

各要素は0〜4で評価する。`derived_score` は並べ替えとレビュー優先度にのみ使う。

```text
base = 25 × (
  0.30 × source_reliability
  0.25 × directness
  0.20 × corroboration
  0.15 × freshness
  0.10 × scope_fit
)

derived_score = max(0, round(base - 10 × conflict_penalty))
```

`conflict_penalty` は0〜2。値にかかわらず重大な反証が未解決なら `epistemic_status: CONTESTED` とする。`derived_score` だけから `FACT` や `VERIFIED` へ昇格してはならない。

### 11.3 認識状態

`VERIFIED` / `SUPPORTED` / `CONTESTED` / `WEAK` / `HYPOTHESIS` / `REJECTED` / `UNRESOLVED`

## 12. インサイト・判断・制作要件

### 12.1 インサイト

```yaml
insight:
  id: IN001
  statement: 不在・欠落・遮蔽が、本人の複数作品を横断する主要操作である
  claim_ids: [CL001, CL008]
  opposing_claim_ids: [CL034]
  epistemic_status: SUPPORTED
  production_implication: 説明文より知覚上の欠落を主要操作の候補にする
```

### 12.2 判断

```yaml
decision:
  id: DC001
  question: 人物の顔を識別可能に表示するか
  selected_option: 顔を識別できない構図を採用する
  rejected_options:
    - 正面顔を使用する
    - モザイク処理を使う
  insight_ids: [IN001]
  evidence_ids: [EV003, EV018]
  reason: 美的傾向、匿名性という主題、肖像権リスクが整合する
  uncertainty: 実寸展示で身体断片だけから他者性が成立するかは未検証
  review_trigger: 観客試験で他者の存在が認識されない場合
  authority: agent-recommended
  status: ADOPTED
```

### 12.3 制作要件

```yaml
requirement:
  id: RQ001
  category: visual
  statement: 人物の顔を識別可能な状態で表示しない
  source_decisions: [DC001]
  priority: mandatory
  acceptance_test_ids: [AT001]
  status: ADOPTED
```

### 12.4 受入試験

```yaml
acceptance_test:
  id: AT001
  target_requirement: RQ001
  method: frame_review
  preconditions: 全出力フレームが揃っている
  pass_condition: 全フレームで人物の顔が識別不能である
  evidence_to_record: reviewed-frame-list
  executor: production-validator
  result: NOT_RUN
```

「不穏にする」「美しくする」のような判定不能な文は、そのまま必須要件にしない。観察可能な形式、比較対象、評価者、試験方法のいずれかを追加する。

### 12.5 Visual language

`05_production/visual-language.yaml`は、研究側の媒体decisionを制作側へ渡すための
typed artifactである。`schemas/visual-language.schema.json`を正本とし、媒体は
`decision-log.yaml`の`ADOPTED`な一意の媒体選択decisionからのみ参照する。自由文から
媒体を推測しない。DRAFT〜DECIDINGではtemplateの空骨格を許可し、
`READY_FOR_PRODUCTION`以降は媒体、技法、適用条件、禁止表現、未解決不確実性の参照を
validatorが検査する。素材・施工・製造・最終権利安全保証はproduction/governance側の責務とする。

## 13. 個人美意識プロファイル

プロファイルは固定人格診断ではなく、時系列の仮説集合として保存する。

```yaml
aesthetic_signal:
  id: AS001
  creator_id: creator-masa
  statement: 対象を直接説明するより欠落によって示す傾向
  evidence_refs: [project/example-project::EV003, project/example-project::EV007]
  observed_period: {from: 2024-01-01, to: 2026-12-31}
  strength: MODERATE
  context: LONG_TERM
  counterexample_refs: [project/example-project::EV034]
  confidence_status: SUPPORTED
  review_after: 2027-02-11
```

The canonical profile template is `templates/profile/aesthetic-signals.yaml`. Real
creator/profile instances are never committed here; `tools/validate.py` and
`tools/build_graph.py` read an explicitly supplied `--profiles-root` (or `<root>/profiles`
when omitted). Signals must resolve project-qualified evidence, and
`observed_period.from <= observed_period.to <= review_after`.

規律:

- 「本人は必ず〜を好む」のような恒久断定をしない。
- 最近の行動と長期傾向を分ける。
- 反例、意図的に破りたい傾向、すでに飽きている形式を保存する。
- メールやカレンダー由来の関心は7〜30日で再確認する。
- 新作完成時に、予測と実際の判断の差分を記録する。

## 14. 検索・取得インターフェース

### 14.1 CLI

```bash
python3 tools/new_project.py <project-id> --root <temporary-work-root>
python3 tools/validate.py --check
python3 tools/build_graph.py
python3 tools/bundle.py project/<project-id> --audience human
python3 tools/bundle.py project/<project-id> --audience production-agent
python3 tools/impact.py --evidence EV001
python3 tools/audit.py
```

### 14.2 将来のAPI・MCP

```text
get_project_summary(project_id)
get_research_questions(project_id, status)
get_relevant_personal_signals(creator_id, query)
get_external_references(project_id, query)
get_claims(project_id, topic, epistemic_status)
get_supporting_evidence(claim_id)
get_counterevidence(claim_id)
get_decisions(project_id)
get_production_requirements(project_id, category)
get_unresolved_questions(project_id)
get_acceptance_tests(project_id)
get_change_impact(evidence_id)
```

初期実装はMarkdown、YAML、JSONL、Git、CLIで完結させる。API、MCP、埋め込み検索は、正本スキーマとCLI検証が安定した後に追加する。

## 15. 検証とCI

### 15.1 `validate.py --check` が拒否するもの

- 必須ファイル、必須項目、IDの欠落
- ID、URI、パスの不一致とID重複
- 存在しない参照、循環してはいけない依存
- 語彙外の状態、主張型、機密区分、権利区分
- 証拠を持たない `FACT`、根拠を持たない制作判断
- 反証があるのに `VERIFIED` とされた主張
- 必須制作要件に受入試験がない状態
- `PRIVATE_RAW` または `RESTRICTED` のGit保存
- 再配布不許可資料の `approved-snapshots/` への格納
- 不正な状態遷移
- 完了状態なのに未解決参照がある状態
- 生成物が正本より古い状態

### 15.2 `audit.py` が警告するもの

監査はcommitを止めず、次に調べることを生成する。

- 単一ソースだけに依存する重要判断
- 反証探索が行われていない主要インサイト
- 長期間更新されていない美意識シグナル
- 同種の過去作品に偏った先行例
- 西洋、英語圏、著名作家等への資料偏在
- 要件へ接続されないインサイト
- 使われていない証拠、孤立した主張
- 未実施の必須受入試験
- 権利・安全性の再確認期限切れ

### 15.3 CI

GitHub Actionsで次を実行する。

1. スキーマ検証
2. 参照整合性検証
3. 機密・権利ルール検証
4. 単体テスト
5. グラフ・索引・被覆表の再生成
6. 生成差分が残る場合は失敗

ローカルフックだけに依存しない。CI未実装の段階では、仕様書に「検証済み」と記載しない。

## 16. 変更影響分析

```text
新証拠または証拠の失効
  → 関連主張
  → 関連インサイト
  → 関連判断
  → 制作要件
  → 試作・受入試験
```

| 影響 | 定義 | 動作 |
|---|---|---|
| `NONE` | 結論・要件に影響しない | 台帳と説明のみ更新 |
| `MINOR` | 補助根拠または表現が変わる | 影響箇所を再生成 |
| `MAJOR` | 判断または必須要件の再評価が必要 | `ANALYZING` へ再開 |
| `CRITICAL` | 中心命題、権利、安全性、公開可否に影響 | 制作を止め、人間承認へ送る |

`dependency-index.json` は生成物とし、手編集しない。

## 17. 鮮度・版管理・保持

### 17.1 バージョン

SemVerを使う。

- MAJOR: 中心命題、権限、成果物互換性を破る変更
- MINOR: 新証拠、分析、判断、要件の追加
- PATCH: 文言、誤記、参照、メタデータの修正

### 17.2 再確認の既定値

| 対象 | 再確認 |
|---|---|
| 長期的な美意識仮説 | 半年または新作完成時 |
| 直近の関心 | 30日 |
| メール・カレンダー由来関心 | 7〜30日 |
| 技術仕様、製品仕様 | 制作開始前 |
| 法令、権利、応募規約 | 公開・応募前 |
| 会場、現地条件 | 設営前 |
| 素材安全性 | 購入ロットまたは仕様変更時 |

保持期限は `config/retention-policy.yaml` で決める。期限到来時は原データを自動削除せず、削除候補と依存関係を報告する。

## 18. 人間向け出力とエージェント向け出力

### 18.1 人間が通常読むもの

- `executive-brief.md`
- `creative-direction.md`
- `decision-log.yaml` の人間向けバンドル
- `prototype-backlog.yaml` の人間向けバンドル
- `completion-report.json` の要約

### 18.2 後続エージェントが読むもの

- `manifest.yaml`
- `claims.jsonl`
- `external-references.jsonl`
- `decision-log.yaml`
- `production-requirements.yaml`
- `acceptance-tests.yaml`
- `production-agent-context.md`
- `research-state.json`

後続エージェントへプロジェクト全体を無条件に渡さない。タスク、必須要件、禁止事項、参照作品、未解決事項、受入試験をまとめた最小コンテキストを渡す。

## 19. 完了基準

### 19.1 プロジェクト完了

次がすべて真であること。

- `manifest.yaml` が存在し、スキーマ検証済み。
- 必須質問が `ANSWERED` または理由付き `UNRESOLVED`。
- すべての証拠に出所、取得日時、権利、機密性がある。
- すべての採用判断がインサイトまたは証拠を参照する。
- すべての必須制作要件が判断と受入試験を参照する。
- 反証と不確実性が明示されている。
- 全ID参照が解決する。
- `PRIVATE_RAW` と `RESTRICTED` がGitに存在しない。
- 権利、安全、プライバシーのレビュー結果がある。
- 終了状態と再開条件を持つ `completion-report.json` がある。
- `config/stopping-policy.yaml` の調査量下限を満たしている。
- 棄却案、不確実性、先行作品との差分、自己反復レビューが記録されている。

調査量または品質レビューが不足している場合の状態は `INCOMPLETE` とする。これは調査後に残る非ブロッキングの未解決事項を示す `COMPLETE_WITH_GAPS` とは別であり、`INCOMPLETE` のプロジェクトからproduction handoffを生成してはならない。既定下限は証拠30、主張18、インサイト4、判断3、要件4で、`01_planning/research-plan.yaml#minimums`に理由を添えて下げられる。

```python
def research_project_is_usable(project):
    return all([
        project.manifest_valid,
        project.mandatory_questions_terminal,
        project.evidence_ledger_valid,
        project.claim_references_resolved,
        project.decisions_traceable,
        project.requirements_testable,
        project.rights_review_complete,
        project.privacy_review_complete,
        project.no_private_raw_in_git,
        project.completion_report_terminal,
        project.research_quality_sufficient,
    ])
```

### 19.2 本システムのMVP完了

- [x] 本書を正本としたディレクトリ構造が存在する。
- [x] 主要7スキーマが実装されている。
- [x] `new_project.py` が完全な雛形を生成する。
- [x] `validate.py --check` が正常系・異常系テストを通る。
- [x] `build_graph.py` が証拠から要件までの依存グラフを生成する。
- [x] `bundle.py` が人間向けと制作エージェント向けの2種類を生成する。
- [x] `impact.py` が証拠IDから影響する要件を列挙する。
- [x] GitHub Actionsが検証と生成物鮮度を確認する。
- [x] サンプルプロジェクト1件が `COMPLETE` または `COMPLETE_WITH_GAPS` へ到達する。
- [x] 生の個人データをGitへ入れずに個人証拠由来の派生シグナルを利用できる。

## 20. 実装順序

### Phase 0: 骨格

1. READMEとディレクトリ作成
2. 語彙・状態・権限・停止規則の設定
3. JSON Schema作成
4. プロジェクト雛形作成

### Phase 1: 検証可能な単体リサーチ

1. `new_project.py`
2. `validate.py`
3. 1件の固定手順書
4. サンプルプロジェクト
5. GitHub Actions

### Phase 2: 追跡と再利用

1. `build_graph.py`
2. `bundle.py`
3. `impact.py`
4. 個人美意識プロファイルへの還流
5. `art-history-notes` 参照アダプタ

### Phase 3: 自律実行

1. Orchestratorの状態遷移
2. タスクキュー、予算、再試行
3. 批評・監査ループ
4. 差分更新
5. 実行ログと再開

### Phase 4: 連携

1. MCP/API
2. キーワード・グラフ・意味検索
3. 制作エージェントへのコンテキスト配布
4. 完成後レビューの自動還流

## 21. 設計上の決定記録

| 論点 | 決定 | 理由 |
|---|---|---|
| 成果物 | 単一レポートではなく多層パッケージ | 再利用、差分更新、監査を可能にするため |
| 一般美術史 | `art-history-notes` を参照 | 重複と正本競合を避けるため |
| 私的原データ | Git保存禁止 | 機密性、第三者情報、履歴残存リスクのため |
| 初期基盤 | Markdown/YAML/JSONL/Git/CLI | 可読性、差分管理、実装可能性を優先するため |
| DB/MCP | 後段 | スキーマ未確定時の二重実装を避けるため |
| 信頼度 | 状態＋根拠＋補助スコア | 偽の精密さを避けながら並べ替え可能にするため |
| 未解決 | 失敗ではなく終端状態 | AIが無限探索または捏造で穴埋めすることを防ぐため |
| 人間確認 | 例外条件だけに限定 | 自律性を保ちつつ権利・安全・公開リスクを制御するため |
| 生成物 | 手編集禁止 | 正本との食い違いを防ぐため |

## 22. 既存 `spec-v0.md` からの変更点

- 出力仕様を、入力・計画・実行・検証を含むシステム全体へ拡張した。
- `evidence/originals/` への無条件保存を廃止し、私的原データのGit保存を禁止した。
- `art-history-notes` との正本分担を定義した。
- 状態機械、停止規則、再試行、エスカレーションを追加した。
- 主張型、認識状態、信頼度算定、反証の扱いを固定した。
- リポジトリ構造、スキーマ、CLI、CI、監査、実装順序を追加した。
- MVPの客観的な完了条件を追加した。

`spec-v0.md` は削除せず設計履歴として保持する。ただし実装判断が本書と競合する場合は本書を正とする。

## 23. 改訂規則

- 要件変更はIssueで理由、影響、代替案を記録してから本書を更新する。
- スキーマ互換性を壊す変更はMAJOR版を上げる。
- 本書、スキーマ、検証ツール、テンプレートが食い違う場合、CIを失敗させる。
- 実装上判明した未決事項は黙って補完せず、Issueへ記録する。

## 24. 改訂履歴

| 版 | 日付 | 内容 |
|---|---|---|
| 1.0.0 | 2026-08-11 | `spec-v0.md` を統合し、実装可能なシステム全体仕様として確定 |
