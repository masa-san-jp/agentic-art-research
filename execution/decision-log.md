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
| 2026-08-11 | ED-017 | RUNTIME-004はtask、role固有source、制約、受入試験だけを含むJSON context packを生成し、未知role・欠損source・不明taskをfail closedする | workerへ不要なプロジェクト全体を渡さず、最小文脈と受入条件を同じ再現可能な契約で配布するため | `tools/context_pack.py`、`tests/test_context_pack.py` |
| 2026-08-11 | ED-018 | INTEGRATION-001は外部checkoutの`data/graph.json`をsource commit固定でread-onlyに読み、stable ID・status・claims・sources・relationsだけを返す | 一般美術史の正本を複製せず、commit変更を再現可能に検知するため | `tools/art_history_adapter.py`、`tests/test_art_history_adapter.py` |
| 2026-08-11 | ED-019 | INTEGRATION-002は上流で承認済みの`PRIVATE_DERIVED`だけをopaque URI・SHA-256・監査メタデータ・固定カテゴリの派生シグナルへ正規化し、`PRIVATE_RAW`、`RESTRICTED`、未知フィールドをfail closedする | 私的原文をrepoやログへ漏らさず、派生結果の出所と承認を再検証可能にするため | `tools/private_evidence_adapter.py`、`schemas/private-evidence-adapter.schema.json`、`tests/test_private_evidence_adapter.py` |
| 2026-08-11 | ED-020 | EVAL-002は固定offline fixtureの期待トレース、state replay、期限切れlease再取得、重複効果抑止、private adapter境界、固定時刻監査を決定論的な評価レポートへ集約する | 単体テストの存在だけでなく、MVPの品質・安全ゲートを同一の受入結果で確認するため | `tools/evaluate.py`、`schemas/evaluation.schema.json`、`tests/test_evaluation.py` |
| 2026-08-11 | ED-021 | RELEASE-001はローカルrelease gateと公開CI run metadataを検証するが、GitHub Releaseの作成・告知は人間承認なしに実行しない | 再現可能な品質判定と外部公開の承認を分離するため | `tools/release_check.py`、`schemas/release-check.schema.json`、`execution/ci-evidence.json`、`docs/release-checklist.md` |
| 2026-08-11 | ED-022 | 明示承認後、v1.0.0を検証済みmain commit `d0f2df8d2e6b639d0c5a45104368943ebfb7d1e7`へ固定して公開し、release taskとstateをterminalへ更新する | 公開対象を検証済みcommitへ固定し、承認済み外部操作と完了記録を追跡可能にするため | GitHub Release v1.0.0、`execution/state.yaml` |
| 2026-08-11 | ED-023 | プロトコルrepositoryと実プロジェクト成果物を分離し、生成物はプロジェクトID単位で外部のAgentic-Art-Outputへ保存する | canonical repositoryを再現可能なプロトコルの正本に保ち、デモ・実データ・生成graphの混入を防ぐため | AGENTS.md、README.md、docs/project-output-boundary.md |
| 2026-08-12 | ED-024 | upstreamのresearch-request受理はcanonical request hashをproject内receiptへ保存し、受理projectを常にRESEARCH_ONLYで開始する | 同一依頼の再試行を安全に冪等化し、未完成のproduction handoffを受理時に生成せず、researchとproductionの責任境界を維持するため | `tools/accept_research_request.py`、`schemas/research-request*.schema.json` |
