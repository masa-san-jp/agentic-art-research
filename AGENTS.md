# Repository instructions

## Mission

設計仕様に従い、証拠から制作要件まで追跡可能なAgentic Art Researchを完成させる。

## Read order

1. `docs/20260811-agentic-art-research-system-design-specification.md`
2. `docs/20260811-agentic-art-research-repository-execution-plan.md`
3. `PLANS.md`
4. `execution/task-queue.yaml`
5. 変更対象に最も近い文書とテスト

## Work protocol

- 複雑な変更は `PLANS.md` に従うExecPlanとして実行する。現在の正本は上記実行計画。
- 依存関係が完了した最小IDの `READY` タスクを選び、原則1タスクずつ完了させる。
- 実装中に実行計画の `Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes` を更新する。
- セッション記憶を前提にしない。別のGPT-5.6 LunaまたはClaude Sonnet級エージェントが、リポジトリだけで再開できる状態を残す。
- 曖昧さは設計仕様、テスト、保守的な既定値の順で解決する。人間確認は仕様書 §6.2 の場合だけ。
- 実装後は `python3 -m unittest discover -s tests -v` と `python3 tools/validate.py --check` を実行する。
- 完了時にタスク状態、実行コマンド、結果、残課題、次の開始点を更新する。

## Engineering rules

- Python 3.11以上。初期段階では依存を最小化する。
- ID、状態、語彙、パスをハードコードで分散させず、`config/` と `schemas/` を参照する。
- 正本は `config/`、`schemas/`、`projects/`、`profiles/`。`data/` は生成物で手編集禁止。
- 失敗を黙って補正しない。入力ファイルと理由を含む明確なエラーを返す。
- 新機能には正常系と失敗系のテストを追加する。
- 既存仕様を破る場合、コードだけでなく設計、スキーマ、移行、テストを更新する。

## Safety

- `PRIVATE_RAW`、`RESTRICTED`、認証情報、個人メール本文、カレンダー詳細、非公開音声をGitへ入れない。
- 外部送信、公開、応募、購入、契約、削除は実行しない。
- 権利不明素材は採用せず、理由付きの棄却またはギャップとして残す。

