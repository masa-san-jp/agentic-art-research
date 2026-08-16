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

チェックボックスとUTCまたはJST日時。未完了、部分完了、完了を正確に表す。

### Surprises & Discoveries

- Production側の現状が`references`/`record_hash`を期待し、Research exporterの`records`/`record_sha256`と不一致だった。canonical hashの計算責任はResearchに置き、Productionは形式と非ゼロ値を検証する。
- Issue #44の実測では、証拠10件・主張5件・インサイト2件・判断2件・要件4件、棄却案0件、先行作品調査0件、自己反復評価0件でも完了・handoffまで到達していた。既存の`COMPLETE_WITH_GAPS`は調査済みの未解決事項を表すため、調査未達とは別の`INCOMPLETE`が必要。

実装中に判明した制約、失敗、想定との差を、短い証拠とともに記録する。

### Decision Log

- 2026-08-14: Production consumerとの実データ検証でsource-ref indexのwire key不一致が判明したため、Issue #43に従い`references`/`record_hash`へ統一する。参照カテゴリとアクセスURLは別Issueへ分離する。
- 2026-08-16: Issue #44の既定下限は証拠30、主張18、インサイト4、判断3、要件4とする。`research-plan.yaml#minimums`で下げられるが、同じ`minimums.reason`を必須にし、既定値は`config/stopping-policy.yaml`に置く。
- 2026-08-16: 下限・棄却案・不確実性・先行作品・自己反復評価のいずれかが不足した場合、core validationが通っていても完了状態を`INCOMPLETE`とする。`COMPLETE_WITH_GAPS`は調査後に残った非ブロッキングgap専用とし、`INCOMPLETE`のhandoffは生成拒否する。
- 2026-08-16: 先行作品は`03_knowledge/prior-art.jsonl`、自己反復評価は`04_decisions/self-repetition-review.yaml`を正本とし、回答者・PRIVATE_RAW本文・作品実体は記録しない。

決定、理由、代替案、影響、日付を記録する。

### Outcomes & Retrospective

- 完了: Research側変更はテスト・validator通過済み。別PRで公開し、Production側consumer変更と同時mergeせず、両方のPR URLを親Issueへ記録してから人間mergeを待つ。
- 完了: `COMPLETION-QUALITY-001`のスキーマ、validator、完了判定、handoff拒否、fixture、テストを実装して検証した。公開ブランチへのpushとPRレビューは次のGitHub操作で行う。

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

## 実行規則

1. 計画全体を読む。
2. `Progress` と `execution/task-queue.yaml` の整合を確認する。
3. 次の未完了マイルストーンだけを実装する。
4. 小さな動作単位でテストする。
5. 発見と決定を即時に計画へ戻す。
6. 受入条件を満たすまで「完了」としない。
7. 終了時に次の正確な開始点を残す。
