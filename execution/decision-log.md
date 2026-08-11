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
