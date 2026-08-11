# Execution decision log

設計仕様を変えない実装判断を記録する。仕様変更はGitHub Issueと設計仕様書の改訂が必要。

| 日付 | ID | 決定 | 理由 | 影響 |
|---|---|---|---|---|
| 2026-08-11 | ED-001 | エージェント状態を会話でなくファイルへ保存 | セッションを跨いで再開するため | `execution/` を正本化 |
| 2026-08-11 | ED-002 | 初期CLIをPython標準ライブラリ＋PyYAMLで実装 | 実行系が理解・修正しやすくするため | 依存を1件に限定 |
| 2026-08-11 | ED-003 | 外部接続のないfixtureをE2Eの基準にする | 外部障害とロジック不良を分離するため | M3前提 |
| 2026-08-11 | ED-004 | Draft 2020-12の参照解決テストに`jsonschema` 4.xを追加 | 共通定義を重複させず、schemaとfixtureを同じ実装で検査するため | `VALIDATE-001`のCLI検証でも再利用 |
| 2026-08-11 | ED-005 | 秘密検出は高信頼度パターンを`access-policy.yaml`に置き、単語単独では拒否しない | 誤検出を抑えながらコミット前に明白な秘密を止めるため | `SECURITY-002`で検出範囲を再評価 |
| 2026-08-11 | ED-006 | 参照整合性と状態遷移の規則をvalidatorで検査し、現在状態は`research-state.json`からのみ読む | ログの推測による状態逆行と孤立成果物を早期に拒否するため | `VALIDATE-002`、後続Orchestratorの契約 |
| 2026-08-11 | ED-007 | TEST-001では正常生成プロジェクトへ単一変異を適用する動的fixtureと、既存スキーマfixtureを一つのYAMLマトリクスで管理 | 全blocking ruleを重複の少ない再現可能な入力として確認するため | `tests/fixtures/validation-matrix.yaml`、`tests/test_fixture_matrix.py` |
| 2026-08-11 | ED-008 | グラフの辺はプロジェクトスコープの `project/<slug>::<local-id>` を参照し、裸のIDの影響分析は一意な場合だけ許可 | 複数プロジェクトに同じローカルIDが存在しても証拠の影響範囲を混同しないため | `tools/build_graph.py`、`tools/impact.py`、`tools/audit.py` |
| 2026-08-11 | ED-009 | BUNDLE-001はaudienceごとの固定相対パスを全件解決し、解決済みソース一覧をbundleへ出力する | 必要文脈の欠落を成功扱いにせず、bundleの範囲を監査可能にするため | `tools/bundle.py`、`tests/test_bundle.py` |
| 2026-08-11 | ED-010 | IMPACT-001は同じレポートモデルをJSONとMarkdownへ変換し、上流・下流をともに深さ付きBFSで列挙する | CLI、機械処理、人間レビューで同じ影響範囲を再利用するため | `tools/impact.py`、`tests/test_graph.py` |
| 2026-08-11 | ED-011 | AUDIT-001の鮮度判定は設定済みreview日数と注入可能な現在時刻で行い、監査結果はexit code 0の警告にする | 時間依存の品質確認を再現可能にし、調査途中のプロジェクトをcommit阻止しないため | `config/retention-policy.yaml`、`tools/audit.py`、`tests/test_audit.py` |
| 2026-08-11 | ED-012 | SAMPLE-001は固定metadataで雛形を生成し、fixture内の正本overlayを適用後、validatorとgraphを再生成する | 同じ入力から同じterminal packageを作り、個人・外部原文を含めないため | `tools/run_project.py`、`tests/fixtures/harmony/`、`tests/test_sample.py` |
| 2026-08-11 | ED-013 | COMPLETE-001は既存または明示されたRFC 3339時刻をcompletion reportと状態更新へ使い、`VALIDATING`からterminal状態へのイベントを追記する | 完了判定を決定論的にし、状態機械と再開情報を同時に保持するため | `tools/complete.py`、`tests/test_complete.py` |
| 2026-08-11 | ED-014 | RUNTIME-001は`config/vocabularies.yaml`からstate machineを構築し、実行時適用とrun-log replayで同じ遷移規則を利用する | validator、completion、将来のOrchestrator間でライフサイクル契約を分散させないため | `tools/state_machine.py`、`tests/test_state_machine.py` |
| 2026-08-11 | ED-015 | RUNTIME-002は`research-plan.yaml`のタスク定義を`research-state.json.task_runtime`へ固定し、leaseとタスクイベントを既存`run-log.jsonl`へ記録する | 中断後に2ファイルだけで再開でき、期限切れworkerの再取得と`effect_key`による完了冪等性を同じ永続契約で検証するため | `tools/task_runtime.py`、`schemas/research-state.schema.json`、`tests/test_task_runtime.py` |
| 2026-08-11 | ED-016 | RUNTIME-003は`run-log.jsonl`の質問イベントを設定済み上限へ集計し、成功条件を優先してから`UNRESOLVED`または`BLOCKED`へ停止し、質問台帳へのapplyを冪等にする | 有限の探索を再現可能に終端化し、既存ライフサイクルイベントとの誤認と二重記録を避けるため | `tools/stopping_policy.py`、`tests/test_stopping_policy.py` |
| 2026-08-11 | ED-017 | RUNTIME-004はロール設定の許可kindとタスクの明示IDを使い、質問から下流成果物を導出してJSON packへ出力する | 後続workerへ全プロジェクトを渡さず、必要な証拠・制約・受入試験だけを監査可能に渡すため | `config/role-context.yaml`、`tools/context_pack.py`、`schemas/context-pack.schema.json`、`tests/test_context_pack.py` |
| 2026-08-11 | ED-018 | INTEGRATION-001は外部KBの`data/graph.json`とsource Git HEADを読み取り、参照ID・URN・commit・取得日時・用途だけを`external-references.jsonl`へ冪等追加する | 一般美術史の正本競合と本文複製を避け、同一commitの再取込を重複させないため | `config/integrations.yaml`、`tools/art_history_adapter.py`、`schemas/external-reference.schema.json`、`tests/test_integration_adapter.py` |
| 2026-08-11 | ED-019 | INTEGRATION-002は個人ソースを解決せず、設定済みschemeのopaque URI・sha256・最小メタデータをホワイトリストで`PRIVATE_DERIVED`証拠へ再構成し、派生値を`PREFERENCE_SIGNAL`主張として同時に記録する | PRIVATE_RAWを読取・保存・ログ出力せず、派生シグナルの根拠だけを再開可能なリポジトリへ残すため | `config/private-evidence.yaml`、`tools/private_evidence.py`、`tests/test_private_evidence.py` |
| 2026-08-11 | ED-020 | EVAL-002はcanonical rootを変更せず、一時root上のoffline fixtureを対象に5ゲートを評価し、結果JSONへ現在時刻を含めない | 評価自体の副作用を防ぎ、CI・再実行・差分比較で同一入力の結果を安定させるため | `config/evaluation.yaml`、`tools/evaluate.py`、`tests/test_evaluation.py`、`.github/workflows/validate.yml` |
