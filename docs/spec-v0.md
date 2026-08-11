AIエージェント実施リサーチの出力・利用仕様書
作成日：2026-07-29
 版：1.0
 位置づけ：自律型アート制作リサーチの成果を、人間と後続AIエージェントが再利用可能にするための出力仕様
0. 結論
実施されたリサーチは、単一のレポートとして出力してはならない。
以下の5層へ分けて出力する。
```text
 1. 原証拠層
 2. 構造化知識層
 3. 判断層
 4. 制作実行層
 5. 閲覧・検索・連携層
 ```
各層の役割は異なる。
|層        |目的                 |主な利用者         |
 |---------|-------------------|--------------|
 |原証拠層     |出典と取得物を保存する        |監査、再検証エージェント  |
 |構造化知識層   |証拠から抽出した主張・関係を保存する |分析、検索、比較エージェント|
 |判断層      |何を採用・棄却したかを明確にする   |制作者、ディレクター    |
 |制作実行層    |作品制作へ直接投入できる仕様に変換する|制作、設計、実装エージェント|
 |閲覧・検索・連携層|人間とAIから利用可能にする     |全利用者          |
最終的に必要なのは「調査結果」ではなく、次の状態である。
> 人間は要点と判断理由を読める。 
 > AIエージェントは必要な証拠、制約、制作要件を機械的に取得できる。 
 > すべての判断は元データまで遡れる。 
 > 新しい資料が追加された場合は、影響を受ける判断だけ再計算できる。
1. 出力全体像
```text
 入力データ
   ↓
 原証拠
   ↓
 主張・観察・関係
   ↓
 インサイト
   ↓
 制作判断
   ↓
 制作要件
   ↓
 試作・受入試験
   ↓
 作品制作
 ```
各段階を別の成果物として保存する。
一つのMarkdownレポートの中にすべてを埋め込むと、以下の問題が起きる。
	●	証拠と解釈を機械的に分離できない
	●	後続エージェントが必要箇所だけ取得できない
	●	新しい証拠による差分更新が難しい
	●	同じ調査を別作品へ再利用しにくい
	●	判断理由を元資料まで追跡しにくい
	●	調査の鮮度と有効期限を管理できない
そのため、人間向け文書と機械向けデータを同時に生成する。
2. 五層出力モデル
2.1 原証拠層
目的
取得した資料、本人データ、観察、実験、インタビュー、画像、映像を改変せず保存する。
保存対象
	●	メモ
	●	メール
	●	カレンダー
	●	過去作品
	●	スケッチ
	●	写真
	●	映像
	●	音声
	●	ウェブページ
	●	PDF
	●	アーカイブ資料
	●	インタビュー
	●	現地記録
	●	実験ログ
	●	センサー値
	●	観客テスト
	●	AI実行ログ
出力
```text
 evidence/
 ├── originals/
 ├── snapshots/
 ├── transcripts/
 ├── metadata/
 └── evidence-ledger.csv
 ```
証拠台帳
```yaml
 evidence:
   id: EV001
   source_type: personal_note
   source_location: string
   creator: string
   created_at: datetime | unknown
   acquired_at: datetime
   content_hash: string
   rights_status: string
   sensitivity: low | medium | high
   related_projects: []
   related_questions: []
   extraction_status: processed | unprocessed | excluded
 ```
利用方法
	●	元資料の確認
	●	引用元の追跡
	●	調査結果の再検証
	●	別モデルによる再解析
	●	将来の作品での再利用
	●	証拠の改変検知
2.2 構造化知識層
目的
原証拠から抽出した内容を、検索・比較・推論可能な形へ変換する。
保存単位
	●	観察
	●	主張
	●	解釈
	●	反証
	●	人物
	●	作品
	●	概念
	●	主題
	●	形式
	●	素材
	●	場所
	●	時代
	●	美的判断
	●	制作操作
	●	関係
出力
```text
 knowledge/
 ├── observations.jsonl
 ├── claims.jsonl
 ├── entities.jsonl
 ├── relationships.jsonl
 ├── contradictions.jsonl
 ├── aesthetic-interest-graph.json
 ├── claim-evidence-graph.json
 └── embeddings/
 ```
主張オブジェクト
```yaml
 claim:
   id: CL001
   statement: string
   type: FACT | SOURCE_CLAIM | INTERPRETATION | INFERENCE
   evidence_ids:
     - EV001
     - EV014
   supporting_claims: []
   opposing_claims: []
   scope: string
   confidence_score: 82
   valid_from: datetime | unknown
   valid_until: datetime | none
   status: supported | contested | weak | rejected
 ```
利用方法
後続エージェントは、レポート全体を読み直さず、次のように問い合わせる。
```text
 「本人が長期的に回避している表現を取得」
 「この作品構想と類似する過去作品を取得」
 「匿名性に関する支持証拠と反証を取得」
 「現在の美意識と過去の美意識の差分を取得」
 ```
2.3 判断層
目的
調査から何を判断したかを明示する。
調査結果が豊富でも、採用・棄却・保留が明確でなければ制作へ使えない。
出力
```text
 decisions/
 ├── insight-register.yaml
 ├── decision-log.yaml
 ├── rejected-options.yaml
 ├── uncertainty-register.yaml
 └── executive-brief.md
 ```
インサイト
```yaml
 insight:
   id: IN001
   statement: >
     本人の過去作品では、対象を直接説明するより、
     不在・欠落・遮蔽によって主題を示す形式が反復されている。
   evidence_ids:
     - EV003
     - EV007
     - EV021
   opposing_evidence_ids:
     - EV034
   confidence_score: 84
   production_implication: >
     新作でも説明文を中心にせず、知覚上の欠落を主要操作とする。
 ```
制作判断
```yaml
 decision:
   id: DC001
   question: 顔を明確に映すか
   selected_option: 顔を識別できない構図を採用
   rejected_options:
     - 正面顔を使用
     - モザイク処理
   evidence_ids:
     - EV003
     - EV018
   insight_ids:
     - IN001
   reason: >
     過去作品の美的傾向、匿名性という主題、肖像権リスクが整合する。
   uncertainty: >
     実寸展示で身体断片だけで他者性が成立するかは未検証。
   review_trigger: >
     観客テストで他者の存在が認識されない場合
 ```
利用方法
	●	制作者が判断理由を確認する
	●	別案へ変更する際の影響を確認する
	●	新しい証拠が判断を覆すか判定する
	●	AIが勝手に過去判断を変更することを防ぐ
	●	採用しなかった案を再利用する
2.4 制作実行層
目的
調査結果を、そのまま制作タスクへ投入できる形へ変換する。
出力
```text
 production/
 ├── creative-direction.md
 ├── production-requirements.yaml
 ├── visual-language.yaml
 ├── material-specification.yaml
 ├── spatial-specification.yaml
 ├── technical-specification.yaml
 ├── rights-and-safety-constraints.yaml
 ├── acceptance-tests.yaml
 ├── prototype-backlog.yaml
 └── production-agent-context.md
 ```
制作要件
```yaml
 requirement:
   id: RQ001
   category: visual
   statement: >
     人物の顔は識別可能な状態で表示しない。
   source_decisions:
     - DC001
   mandatory: true
   acceptance_test:
     method: frame_review
     pass_condition: >
       全フレームで人物の顔が識別不能である。
   status: adopted
 ```
クリエイティブ・ディレクション
人間向けには、以下のように短く出力する。
```md
 ## 作品の核

 他者が大量に存在するにもかかわらず、
 相互認識が成立しない都市状態を扱う。

 ## 継承する本人の美意識

 - 直接説明より欠落で示す
 - 人物全体より身体断片を使う
 - 観客に意味を確定させすぎない
 - 音と視覚の不一致を利用する

 ## 今回、意図的に破る傾向

 - 過去作品の静的構図
 - 単一視点
 - 観客を外部に置く展示形式
 ```
エージェント用コンテキスト
後続の画像、映像、音響、空間設計エージェントには、レポート全文ではなく必要な制約だけを渡す。
```yaml
 agent_context:
   task: 映像絵コンテ生成
   central_thesis: string
   mandatory_requirements:
     - RQ001
     - RQ004
   preferred_aesthetic_patterns:
     - AP003
     - AP009
   prohibited_patterns:
     - AP012
   reference_works:
     - ART003
     - ART017
   unresolved_questions:
     - Q021
   output_acceptance_tests:
     - AT001
     - AT004
 ```
2.5 閲覧・検索・連携層
目的
出力物を、人間とAIが実際に利用できる状態にする。
提供インターフェース
人間向け
	1.	エグゼクティブ・ブリーフ
	2.	最終リサーチ・ドシエ
	3.	判断一覧
	4.	制作要件一覧
	5.	証拠ブラウザ
	6.	美意識・関心マップ
	7.	未解決事項一覧
	8.	試作バックログ
AI向け
	1.	JSON / YAML
	2.	JSONL
	3.	CSV
	4.	ベクトル検索
	5.	グラフ検索
	6.	API
	7.	MCPサーバー
	8.	エージェント用コンテキストパック
3. 利用者別の出力
3.1 制作者本人
必要なもの：
	●	今回の調査で分かったこと
	●	自分の過去作品との接続
	●	自分の美意識として推定されたもの
	●	どの推定に確信がないか
	●	採用・棄却された案
	●	次に何を作るか
	●	何を確認すれば完成へ進めるか
主な出力：
```text
 executive-brief.md
 creative-direction.md
 decision-log.md
 prototype-backlog.md
 ```
3.2 リサーチエージェント
必要なもの：
	●	未解決質問
	●	既取得資料
	●	既実行検索
	●	資料品質
	●	残り予算
	●	再調査条件
主な出力：
```text
 question-register.yaml
 source-ledger.csv
 gap-register.yaml
 research-state.json
 ```
3.3 企画・コンセプトエージェント
必要なもの：
	●	中心命題
	●	関心領域
	●	過去作品の系譜
	●	美的緊張
	●	反復している形式
	●	意図的に破る対象
主な出力：
```text
 project-personal-evidence-package.md
 aesthetic-interest-graph.json
 insight-register.yaml
 creative-direction.md
 ```
3.4 制作エージェント
必要なもの：
	●	制作要件
	●	禁止事項
	●	技術条件
	●	素材条件
	●	参考作品
	●	受入試験
主な出力：
```text
 production-requirements.yaml
 technical-specification.yaml
 material-specification.yaml
 acceptance-tests.yaml
 ```
3.5 批評・監査エージェント
必要なもの：
	●	主張と証拠の対応
	●	反証
	●	不確実性
	●	権利
	●	安全性
	●	採用理由
主な出力：
```text
 claim-evidence-graph.json
 contradiction-register.yaml
 uncertainty-register.yaml
 rights-and-safety-constraints.yaml
 audit-report.md
 ```
4. 標準成果物セット
一回のリサーチ完了時に、以下を必ず生成する。
```text
 research-package/
 ├── README.md
 ├── manifest.yaml
 │
 ├── 01_summary/
 │   ├── executive-brief.md
 │   └── final-research-dossier.md
 │
 ├── 02_evidence/
 │   ├── evidence-ledger.csv
 │   ├── personal-evidence/
 │   ├── external-evidence/
 │   └── source-snapshots/
 │
 ├── 03_knowledge/
 │   ├── observations.jsonl
 │   ├── claims.jsonl
 │   ├── entities.jsonl
 │   ├── relationships.jsonl
 │   ├── aesthetic-interest-graph.json
 │   └── claim-evidence-graph.json
 │
 ├── 04_decisions/
 │   ├── insight-register.yaml
 │   ├── decision-log.yaml
 │   ├── rejected-options.yaml
 │   └── uncertainty-register.yaml
 │
 ├── 05_production/
 │   ├── creative-direction.md
 │   ├── production-requirements.yaml
 │   ├── acceptance-tests.yaml
 │   ├── prototype-backlog.yaml
 │   └── production-agent-context.md
 │
 ├── 06_governance/
 │   ├── rights-register.csv
 │   ├── ethics-review.md
 │   ├── safety-risk-register.yaml
 │   └── access-control.yaml
 │
 └── 07_runtime/
     ├── research-state.json
     ├── completion-report.json
     ├── change-log.jsonl
     └── dependency-index.json
 ```
5. manifest.yaml
全成果物の入口になる。
```yaml
 research_package:
   package_id: RP001
   project_id: ART001
   title: 群衆の中の匿名性
   version: 1.0.0
   generated_at: 2026-07-29T09:00:00+09:00
   status: COMPLETE_WITH_EXTERNAL_GAPS

 entry_points:
   human_summary: 01_summary/executive-brief.md
   full_report: 01_summary/final-research-dossier.md
   creative_direction: 05_production/creative-direction.md
   production_context: 05_production/production-agent-context.md
   evidence_ledger: 02_evidence/evidence-ledger.csv
   knowledge_graph: 03_knowledge/claim-evidence-graph.json
   decision_log: 04_decisions/decision-log.yaml
   completion_report: 07_runtime/completion-report.json

 counts:
   evidence: 132
   claims: 74
   insights: 14
   decisions: 9
   requirements: 18
   unresolved_questions: 3

 access:
   classification: private
   allowed_agents:
     - research
     - ideation
     - production
     - audit
 ```
後続エージェントは、最初にmanifest.yamlを読み、必要な成果物だけを取得する。
6. 検索可能にする方法
6.1 キーワード検索
対象：
	●	原文
	●	証拠メタデータ
	●	主張
	●	インサイト
	●	判断
	●	制作要件
用途：
	●	固有名詞
	●	素材名
	●	作品名
	●	明示的な概念
6.2 ベクトル検索
対象：
	●	メモ
	●	作品説明
	●	過去の文章
	●	インタビュー
	●	インサイト
	●	クリエイティブ・ディレクション
用途：
	●	意味的に近い過去作品
	●	同じ感覚を扱う文章
	●	類似する美的判断
	●	言葉が異なる関連概念
6.3 グラフ検索
対象：
	●	証拠
	●	主張
	●	インサイト
	●	判断
	●	制作要件
用途：
```text
 「この制作要件はどの証拠から生まれたか」
 「この証拠が無効になった場合、何が影響を受けるか」
 「匿名性と遮蔽をつなぐ過去作品は何か」
 ```
6.4 時系列検索
対象：
	●	関心
	●	美意識
	●	作品
	●	発言
	●	制作判断
用途：
	●	直近で増えた関心
	●	長期間安定している傾向
	●	変化した価値観
	●	過去には強かったが現在は弱い主題
7. API・MCPとしての公開
リサーチパッケージを後続エージェントから利用する場合、次の操作を提供する。
```text
 get_project_summary(project_id)
 get_personal_aesthetic_profile(project_id)
 get_relevant_past_works(project_id, query)
 get_claims(project_id, topic)
 get_supporting_evidence(claim_id)
 get_counterevidence(claim_id)
 get_decisions(project_id)
 get_production_requirements(project_id, category)
 get_prohibited_patterns(project_id)
 get_unresolved_questions(project_id)
 get_acceptance_tests(project_id)
 get_change_impact(evidence_id)
 ```
応答例
```yaml
 result:
   requirement_id: RQ001
   statement: 人物の顔は識別可能な状態で表示しない
   mandatory: true
   confidence_score: 87
   evidence_ids:
     - EV003
     - EV018
   decision_id: DC001
   acceptance_test_id: AT001
 ```
8. 利用フロー
8.1 新作の企画
```text
 新しい制作テーマ
 → 関連する過去作品・メモ・関心を検索
 → 美意識プロファイルを取得
 → 先行作品調査と統合
 → コンセプト候補を生成
 ```
出力
	●	コンセプト候補
	●	過去作品との連続性
	●	意図的な断絶
	●	既視感・自己反復リスク
8.2 制作案の選定
```text
 複数案
 → 制作要件との適合性評価
 → 本人の美意識との整合性評価
 → 新規性・実現性・権利・安全性評価
 → 採用・保留・棄却
 ```
評価結果は数値だけでなく、根拠を返す。
8.3 制作中のレビュー
```text
 試作
 → 受入試験
 → 制作要件と比較
 → 不一致を検出
 → 修正案を生成
 → 再試験
 ```
リサーチ結果は制作開始前だけでなく、制作中の評価基準として使う。
8.4 完成後の更新
```text
 完成作品
 → 実際の制作判断を記録
 → 観客反応を追加
 → 当初仮説と比較
 → 美意識・関心グラフを更新
 → 次作品の個人一次証拠へ追加
 ```
作品制作そのものが次回リサーチの証拠になる。
9. 鮮度・版管理
9.1 バージョン
```text
 1.0.0
 │ │ └─ 文言・軽微修正
 │ └─── 新しい証拠、分析、要件追加
 └───── 中心命題・制作方向の変更
 ```
9.2 証拠の鮮度
|証拠           |更新判断       |
 |-------------|-----------|
 |本人の長期的美意識    |半年または新作完成時 |
 |直近の関心        |30日        |
 |カレンダー・メール由来関心|7〜30日      |
 |技術仕様         |制作開始前に再確認  |
 |法令・権利        |公開・展示前に再確認 |
 |現地情報         |設営前に再確認    |
 |素材安全性        |購入ロット・仕様変更時|
9.3 失効
各主張・判断に次を持たせる。
```yaml
 validity:
   valid_from: datetime
   review_after: datetime | none
   invalidation_triggers:
     - new_personal_work
     - venue_change
     - material_change
     - legal_change
     - audience_test_failure
 ```
10. 変更影響分析
新しい証拠が追加された場合、全調査をやり直さない。
```text
 新証拠
 → 関連主張を特定
 → 信頼スコアを再計算
 → 関連インサイトを更新
 → 影響する判断を特定
 → 制作要件・試作へ通知
 ```
影響レベル
|レベル       |内容              |
 |----------|----------------|
 |`NONE`    |判断に影響しない        |
 |`MINOR`   |説明・補助証拠だけ更新     |
 |`MAJOR`   |制作要件の再評価が必要     |
 |`CRITICAL`|中心命題または安全・権利を再判断|
11. 権限と機密性
個人メモ、メール、カレンダーには制作と無関係な機密情報が含まれる。
出力区分
|区分                |内容                   |
 |------------------|---------------------|
 |`PRIVATE_RAW`     |原文・原データ。本人と限定エージェントのみ|
 |`PRIVATE_DERIVED` |美意識・関心等の派生情報         |
 |`PROJECT_INTERNAL`|制作チームで共有可能           |
 |`PUBLIC_CITABLE`  |公開・引用可能              |
 |`RESTRICTED`      |利用不可または特別承認が必要       |
外部共有するリサーチ・ドシエには、原メールや個人予定を直接含めず、必要な派生情報だけを記載する。
12. 完了基準
リサーチ結果が「利用可能」と判定される条件は以下である。
人間利用
	●	エグゼクティブ・ブリーフがある
	●	主要判断と理由が読める
	●	不確実性が明示されている
	●	次の制作行動が分かる
AI利用
	●	manifest.yamlがある
	●	必須データがスキーマ検証済み
	●	すべてのID参照が解決する
	●	制作要件に受入試験がある
	●	証拠から判断まで追跡できる
	●	権限区分が設定されている
再利用
	●	プロジェクト固有情報と汎用知識が分離されている
	●	過去作品・美意識データが個人証拠基盤へ還流している
	●	別プロジェクトから意味検索できる
	●	新証拠による差分更新が可能
終了
以下がすべて真なら、出力工程を完了する。
```python
 def research_output_is_usable(package):
     return all([
         package.manifest_exists,
         package.executive_brief_exists,
         package.evidence_ledger_valid,
         package.claim_graph_valid,
         package.decision_log_valid,
         package.production_requirements_valid,
         package.acceptance_tests_valid,
         package.permissions_defined,
         package.all_references_resolved,
         package.completion_report_terminal
     ])
 ```
13. 最終的な利用イメージ
AIエージェントへ次のように依頼できる。
```text
 「今回の作品に関係する私の過去作品と美意識を取得し、
 先行作品調査を踏まえて3案作る」

 「案Bが私の過去の制作傾向と似すぎていないか調べる」

 「採用済みの制作要件だけを映像生成エージェントへ渡す」

 「この試作が調査上の判断と矛盾している箇所を指摘する」

 「新しい展示会場の条件によって無効になる制作要件を抽出する」

 「完成作品と当初の美意識仮説の差分を記録する」
 ```
人間は次のファイルだけを読めばよい。
```text
 executive-brief.md
 creative-direction.md
 decision-log.md
 prototype-backlog.md
 ```
AIは次を中心に利用する。
```text
 manifest.yaml
 claim-evidence-graph.json
 aesthetic-interest-graph.json
 production-requirements.yaml
 acceptance-tests.yaml
 research-state.json
 ```
14. 設計原則
	1.	レポートは閲覧用であり、知識基盤そのものではない
	2.	原証拠、解釈、判断、制作要件を分離する
	3.	人間向けMarkdownとAI向け構造化データを同時生成する
	4.	後続エージェントには必要なコンテキストだけを渡す
	5.	全判断を元証拠まで追跡可能にする
	6.	採用案だけでなく棄却案と理由も保存する
	7.	美意識は固定プロフィールではなく時系列で更新する
	8.	新しい証拠が追加された場合は差分更新する
	9.	制作中・完成後の結果を次回リサーチへ還流する
	10.	出力は、読める、検索できる、実行できる、監査できる状態で初めて完了とする
15. 改訂履歴
|版  |日付        |内容                 |
 |---|----------|-------------------|
 |1.0|2026-07-29|リサーチ成果の出力・利用仕様を初版作成|

