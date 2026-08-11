# Agentic Art Research リポジトリ完成実行計画

- 作成日: 2026-08-11
- 状態: ACTIVE
- 対応仕様: `docs/20260811-agentic-art-research-system-design-specification.md`
- 実行対象: GPT-5.6 LunaまたはClaude Sonnet級の、ファイル編集・コマンド実行・Git操作が可能なエージェント

## Purpose / Big Picture

完成後は、エージェントが制作テーマから新規リサーチプロジェクトを作成し、有限の調査計画を実行し、証拠から制作要件までを追跡し、試作の受入試験と完了報告を生成できる。

利用者は次で確認する。

```bash
python3 tools/new_project.py harmony-study --title "Harmony Study"
python3 tools/validate.py --check
python3 tools/run_project.py harmony-study --offline-fixture tests/fixtures/harmony
python3 tools/build_graph.py
python3 tools/impact.py --evidence EV001
python3 tools/bundle.py project/harmony-study --audience human
```

最後の状態は `COMPLETE` または `COMPLETE_WITH_GAPS` であり、`BLOCKED` の場合は解除条件が機械可読で残る。

## Agent operating contract

- 過去の会話を前提にしない。
- `AGENTS.md`、本計画、タスクキュー、設計仕様を正本とする。
- 1タスクを完了し、検証し、状態を更新してから次へ進む。
- 仕様書にない新規方針を黙って導入しない。可逆で保守的な実装を選び、決定ログへ残す。
- 人間への質問は、設計仕様書 §6.2 のエスカレーション条件だけに限定する。
- 外部接続がなくてもfixtureで全工程を検証できるようにする。

## Progress

- [x] (2026-08-11) M0: リポジトリ骨格、エージェント規則、実行計画、設定、初期スキーマを作成。
- [x] (2026-08-11) M1a: `new_project.py`、基礎validator、テスト、CIの初期版を作成。
- [x] (2026-08-11) `SCHEMA-001`: 7ドメインのDraft 2020-12スキーマ、正常fixture、規則別異常fixtureを追加。
- [x] (2026-08-11) `VALIDATE-001`: JSON Schema、JSONL行番号、YAML重複キーの検証とremediation付きエラーを追加。
- [x] (2026-08-11) `SECURITY-001`: 禁止ファイル名・拡張子、秘密パターン、私的スナップショット境界を検査。
- [x] (2026-08-11) `VALIDATE-002`: 参照ID、重複／循環、質問終端、状態遷移、必須要件の試験接続を検査。
- [x] (2026-08-11) `TEST-001`: 正常fixtureと31種類のblocking validation ruleを宣言的マトリクスで検証。
- [ ] M1b: 全JSON Schema検証と参照整合性を実装。
- [ ] M2: 依存グラフ、バンドル、影響分析、監査を実用レベルへ完成。
- [ ] M3: 代表サンプルプロジェクトを固定fixtureで完走。
- [ ] M4: 状態機械と自律Orchestratorを実装。
- [ ] M5: `art-history-notes` と個人証拠保管先のアダプタを実装。
- [ ] M6: 評価、セキュリティ、回帰、リリース判定を完成。

## Surprises & Discoveries

- 2026-08-11: 元のリポジトリは設計文書中心で、実行系・テスト・CIが未作成だった。このため、機能実装より先に再開可能な計画と機械可読キューが必要。
- 2026-08-11: 個人証拠の原文をGitへ置く構造は不可。テストは合成fixture、実運用は不透明URIとハッシュを使う。
- 2026-08-11: 実行系モデルのベンダー差を吸収するには、モデル固有プロンプトより、短い恒久規則、自己完結計画、テスト、状態ファイルの組合せが重要。
- 2026-08-11: スキーマのDraft 2020-12参照解決とfixture検証には `jsonschema` が必要だった。依存をrequirementsへ追加し、CLIへの統合は `VALIDATE-001` で行う。
- 2026-08-11: 秘密スキャンは高信頼度パターンだけを設定から読み込み、合成秘密をテストコードへリテラル保存しない。一般語の検索だけでは誤検出が多く、単語＋代入形式に限定した。
- 2026-08-11: 状態遷移はログから現在状態を推測せず、`research-state.json`を正本として、存在する遷移イベントだけを設定済みDAGと照合する。
- 2026-08-11: スキーマfixtureだけでは参照・状態・安全境界のblocking ruleを一覧化できないため、生成した正常プロジェクトへ名前付き変異を適用するfixtureマトリクスを追加した。

## Decision Log

| 日付 | 決定 | 理由 |
|---|---|---|
| 2026-08-11 | `AGENTS.md`、`PLANS.md`、個別ExecPlan、YAMLキューを分離 | 常時読む規則を短くし、長期計画と機械処理を両立するため |
| 2026-08-11 | Python 3.11とPyYAMLだけで開始 | Luna/Sonnet級エージェントが依存問題を解きやすくするため |
| 2026-08-11 | 外部接続なしのfixture完走を必須にする | 認証、API停止、ネットワーク制約と機能不良を分離するため |
| 2026-08-11 | 1タスク1責務、依存DAG、状態遷移を固定 | 自律実行の暴走と重複実装を防ぐため |
| 2026-08-11 | Draft 2020-12の参照解決テストに `jsonschema` 4.xを追加 | 共通定義を重複させず、schemaとfixtureを同じ実装で検査するため |
| 2026-08-11 | TEST-001の動的失敗fixtureは、完全なプロジェクトsnapshotを複製せず、正常生成後の単一変異として定義 | fixtureの重複と機密データ混入を避け、各ruleの原因を一つに保つため |

## Outcomes & Retrospective

M0/M1a、`SCHEMA-001`、`VALIDATE-001`、`SECURITY-001`、`VALIDATE-002`、`TEST-001`完了時点では、プロジェクト雛形の生成、Draft 2020-12スキーマ検証、JSONL行番号付きエラー、YAML重複キー検出、機密・秘密境界検査、参照整合性、状態遷移と試験接続の検査、blocking ruleの正常・失敗fixtureマトリクス、CI初期版が実行可能になる。サンプルE2E、調査Orchestratorと外部アダプタは未実装であり、「自律リサーチ完成」とはまだ呼ばない。

## Context and Orientation

- 設計の正本: `docs/20260811-agentic-art-research-system-design-specification.md`
- 実行順序の正本: `execution/task-queue.yaml`
- 現在状態: `execution/state.yaml`
- 変更判断: `execution/decision-log.md`
- 語彙と方針: `config/`
- データ構造: `schemas/`
- プロジェクト雛形: `templates/project/`
- 正本データ: `projects/` と `profiles/`
- 生成物: `data/`

「validator」は、構造・語彙・参照・機密性を検査し、不正な入力を非ゼロ終了で拒否するCLI。「Orchestrator」は、状態とキューに基づいて調査工程を順に進める実行系。「fixture」は、外部サービスなしで同じ入力を再現するテストデータである。

## Milestone M0: Bootstrap

### Goal

別セッションの実行系エージェントが、何を、どの順序で、何をもって完了とするか判断できる。

### Acceptance

- README、AGENTS、PLANS、本計画、機械可読キューが存在する。
- 設計仕様の全Phaseがタスクへ分解され、依存関係と受入条件を持つ。
- `PRIVATE_RAW` をGitへ保存しない規則が文書・設定・validatorで一致する。

## Milestone M1: Contract and validation

### Goal

不正なプロジェクトを早期に拒否し、正しい空プロジェクトを決定論的に生成する。

### Work

1. `SCHEMA-001`: 全スキーマをDraft 2020-12として完成し、共通定義を重複させない。
2. `VALIDATE-001`: JSON Schema検証、JSONL行番号付きエラー、YAML重複キー検知を実装。
3. `VALIDATE-002`: ID参照、状態遷移、必須質問終端、要件と受入試験の接続を検査。
4. `SECURITY-001`: forbidden extension、秘密らしい文字列、私的区分の保存先を検査。
5. `TEST-001`: 正常fixtureと各失敗規則のfixtureを追加。

### Acceptance

```bash
python3 tools/new_project.py smoke-project --title Smoke
python3 tools/validate.py --check
python3 -m unittest discover -s tests -v
```

すべてexit 0。不正fixtureは期待した1規則だけでexit 1になる。

## Milestone M2: Traceability

### Goal

証拠IDから下流の主張、インサイト、判断、要件、試験まで到達できる。

### Work

1. `GRAPH-001`: 正本から有向依存グラフを決定論的に生成。
2. `BUNDLE-001`: human、research-agent、production-agent、auditの4視点をMarkdownで生成。
3. `IMPACT-001`: upstream/downstream影響をJSONとMarkdownで出力。
4. `AUDIT-001`: 孤立、単一出典依存、反証未探索、期限切れ、未実施試験を警告。
5. `TEST-002`: 同一入力でbyte-identicalな生成物になることを検証。

### Acceptance

`EV001 → CL001 → IN001 → DC001 → RQ001 → AT001` がCLI出力で確認でき、壊れた参照はvalidatorが拒否する。

## Milestone M3: Representative project

### Goal

合成データだけで、受付から完了報告まで1件が終わる。

### Work

1. `SAMPLE-001`: 小さな作品テーマと公開利用可能な合成証拠を作る。
2. `PROTOCOL-001`: 1質問1タスクの調査手順を実データで検証。
3. `COMPLETE-001`: `COMPLETE_WITH_GAPS` を含む終了判定を実装。
4. `EVAL-001`: 追跡率、参照解決率、必須試験率を算出。

### Acceptance

fixtureから同じ完了パッケージを再生成でき、採用要件の追跡率が100%。

## Milestone M4: Autonomous orchestrator

### Goal

実行系エージェントが人間へ次工程を聞かず、有限時間で終端状態へ到達する。

### Work

1. `RUNTIME-001`: 状態遷移とイベントログ。
2. `RUNTIME-002`: 依存DAG、リース、再試行、失敗分類、再開。
3. `RUNTIME-003`: 停止規則と探索飽和。
4. `RUNTIME-004`: ロール別コンテキストパック。
5. `RUNTIME-005`: dry-run、offline-fixture、liveの3モード。

### Acceptance

- 中断後に `run-log.jsonl` と `research-state.json` だけで再開できる。
- 同じ失敗を3回以上反復しない。
- 全実行が `COMPLETE`、`COMPLETE_WITH_GAPS`、`BLOCKED`、`CANCELLED` のいずれかで終了する。

## Milestone M5: Integrations

### Goal

一般美術史と私的証拠を正本競合・漏えいなしに利用する。

### Work

1. `INTEGRATION-001`: `art-history-notes` のbundle/graph読み取りアダプタ。
2. `INTEGRATION-002`: commit SHA付き外部参照と鮮度検査。
3. `INTEGRATION-003`: opaque URIだけを扱う個人証拠アダプタ境界。
4. `INTEGRATION-004`: fake adapterによる契約テスト。

### Acceptance

外部KB更新による差分が影響分析へ入り、私的原文がrepoとログへ出ない。

## Milestone M6: Release gate

### Goal

自律実行を継続運用できる品質と安全性を証明する。

### Work

1. `EVAL-002`: 正確性、追跡性、終端性、再開性、安全性のeval。
2. `SECURITY-002`: secrets、path traversal、symlink、unsafe archiveの検査。
3. `CHAOS-001`: API停止、壊れたJSONL、途中kill、重複実行の試験。
4. `DOCS-001`: 導入、運用、障害対応、モデル別開始プロンプト。
5. `RELEASE-001`: v1.0.0チェックリスト。

### Acceptance

設計仕様書 §19.2の全項目がチェック済みで、fixtureによるE2EとCIが連続3回成功する。

## Concrete Steps

各タスクで共通:

```bash
git status --short
python3 -m unittest discover -s tests -v
python3 tools/validate.py --check
```

生成物を変更するタスク:

```bash
python3 tools/build_graph.py
git diff --exit-code data/ || true
python3 tools/validate.py --check
```

タスク終了時は `execution/task-queue.yaml`、`execution/state.yaml`、本書の該当セクションを更新する。

## Validation and Acceptance

最低ゲート:

1. Unit: 個別関数と失敗規則。
2. Contract: スキーマ、アダプタ、CLIの入出力。
3. Integration: 1プロジェクト内の参照連鎖。
4. E2E: offline fixtureから終端状態。
5. Safety: 私的原文、秘密、権利不明素材がGitへ入らない。
6. Determinism: 同一入力から同一生成物。

## Idempotence and Recovery

- `new_project.py` は既存ディレクトリを上書きしない。
- ビルド系は一時ファイルへ書いてから置換する。
- run eventには一意IDを持たせ、重複適用を拒否する。
- task leaseは期限付きとし、異常終了後に再取得できる。
- `git reset --hard`、`git checkout --`、`git restore`、`git stash` で他者の作業を消さない。
- 生成物だけが壊れた場合は正本から再生成し、正本を生成物に合わせて戻さない。

## Interfaces and Dependencies

- Python: 3.11以上
- Runtime dependencies: PyYAML 6.x、jsonschema 4.x
- CLI exit: 0=成功、1=検証不合格、2=利用方法または設定エラー、3=外部依存ブロック
- ID: `<prefix><zero-padded-number>` または `project/<slug>`。正規表現はschemaを正本にする。
- 時刻: RFC 3339、タイムゾーン必須。
- JSONL: 1行1object、UTF-8、行順は生成時にIDで安定化する。
