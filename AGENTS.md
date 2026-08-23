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

## 無人でプロジェクトを進めるとき

調査プロジェクトの中で作業するエージェントは、この節だけで動けるようにする。仕様書と手順書を
全文読んでから組み立てる必要は無い——**入口が、そのタスクに要る分だけを返す。**

```
python3 tools/next_action.py project/<slug> --worker <id> --now <RFC3339>
```

返る JSON が、そのターンの全てである。

- **入口はこれだけにする。** 自分でタスクを選ばない。同じ worker が既に持っているタスクがあれば
  それが返る（`TASK_RESUMED`）ので、途中で落ちても取り直しにならない。
- **書いてよい場所は `write_targets` だけ。** そこに無いファイルを書き換えない。
- **探索は記録してから進む。** `tools/log_event.py` で `SEARCH_ATTEMPT` / `SOURCE_REVIEWED` /
  `EVIDENCE_ROUND` / `ANSWER_FOUND` を残す。停止判定と予算はこの記録だけを見ているので、
  **記録しない探索は、していない探索と区別が付かない。**
- **長い作業では心拍を打つ。** 1つのタスクが lease（既定1800秒）より長くかかるなら、その間に
  `python3 tools/task_runtime.py project/<slug> heartbeat --task-id <id> --worker-id <id> --lease-token <token> --now <RFC3339>`
  で lease を延ばす。**期限切れは作業の失敗ではないので試行回数を減らさない**が、他の作業者が
  そのタスクを取れる状態になるので、握ったまま黙っていない。
- **`acceptance` のコマンドを全部通してから完了させる。** 通らないまま `complete` しない。
- **判断は `decision-log.yaml` に残す。** `authority: agent-recommended`、理由、棄却案を書く。
  棄却案が無い判断は、選んでいない。参照した対象の commit も書く。
- **`forbidden` に挙がった操作をしない。** `operations` はリポジトリ全体の境界、
  `project_prohibited_actions` はそのプロジェクト固有の禁止事項。
- **`budget_remaining.exceeded` が空でないなら、そこで打ち切る。** 質問を `ANSWERED` か
  `UNRESOLVED` で終端させて返す。上限を越えて調べ続けない。
- 全タスクが終わると入口は `NO_TASK_READY` と次の工程名を返す。そこから先も人を待たない。

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
