# Repository instructions

## Mission

設計仕様に従い、証拠から制作要件まで追跡可能なAgentic Art Researchを完成させる。

## Repository/output boundary

- このリポジトリは常にプロトコル、設定、スキーマ、検証器、テストの正本として扱う。デモや実際の制作リサーチ成果物を常設しない。
- 実際のプロジェクト成果物は、プロジェクトIDごとに /Users/masa/マイドライブ/AI-Agent-Pipeline/Agentic-Art-Output/<project-id>/ へ保存する。
- プロジェクト生成・fixture展開・graph生成は一時cloneまたは一時作業rootで実行し、検証後にプロジェクト単位のフォルダだけを出力先へ蓄積する。
- protocol repositoryのprojects/に実プロジェクトを追加せず、data/へ実プロジェクト由来のgraphを残さない。

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
- canonical repositoryに実プロジェクトを置かない。projects/とdata/のmaterializationは一時作業rootまたは外部出力rootだけで行う。
- 失敗を黙って補正しない。入力ファイルと理由を含む明確なエラーを返す。
- 新機能には正常系と失敗系のテストを追加する。
- 既存仕様を破る場合、コードだけでなく設計、スキーマ、移行、テストを更新する。

## Safety

- `PRIVATE_RAW`、`RESTRICTED`、認証情報、個人メール本文、カレンダー詳細、非公開音声をGitへ入れない。
- 外部送信、公開、応募、購入、契約、削除は実行しない。
- 権利不明素材は採用せず、理由付きの棄却またはギャップとして残す。

## Production handoff extension

- `EXTENSION-*`、`HANDOFF-*`、`FEEDBACK-*`タスクでは、基礎仕様の後に`docs/20260811-agentic-art-research-production-handoff-extension-specification.md`を読む。
- 同タスクのExecPlan正本は`docs/20260811-agentic-art-research-production-handoff-execution-plan.md`とする。
- 実装開始点は`execution/task-queue.yaml`にある依存完了済みの最小ID `READY`タスクとし、基礎M0〜M6を再オープンしない。
