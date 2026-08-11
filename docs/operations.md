# Operations

この文書は、リポジトリを安全に導入・運用し、障害から再開するための手順を定義する。
正本は `config/`、`schemas/`、`projects/`、`profiles/` にあり、`data/` は生成物として扱う。
外部送信、公開、応募、購入、契約、削除は自動化しない。設計仕様書 §6.2 に該当する人間確認だけを停止条件とする。

## Onboarding

最初に `AGENTS.md`、設計仕様書、実行計画、`PLANS.md`、`execution/task-queue.yaml` の順に読む。
Python 3.11 以上の仮想環境を作成し、依存関係をインストールする。

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
```

安全境界と正本を確認する。

```bash
python3 -m compileall -q tools tests
python3 tools/security_check.py --check
python3 tools/validate.py --check
python3 -m unittest discover -s tests -v
python3 tools/build_graph.py --check
python3 tools/evaluate.py --offline-fixture tests/fixtures/harmony
python3 tools/docs_check.py --check
```

## Normal run

新規プロジェクトは雛形から作成し、正本ファイルを編集してから検証する。

```bash
python3 tools/new_project.py example-project --title "Example Project"
python3 tools/task_runtime.py project/example-project init --now 2026-08-11T00:00:00+09:00
python3 tools/task_runtime.py project/example-project claim --worker-id worker-a
python3 tools/build_graph.py
python3 tools/bundle.py project/example-project --audience human
python3 tools/audit.py
```

タスクは `execution/task-queue.yaml` の依存関係とID順に従い、1件ずつ claim する。
停止時は進捗、実行コマンド、結果、残課題、`resume_from` を `execution/state.yaml` に残す。
期限切れleaseだけを再開し、同じ `effect_key` の効果を二重適用しない。

個人証拠や非公開音声はリポジトリへコピーせず、許可されたアダプタの不透明URI、ハッシュ、メタデータ、派生シグナルだけを扱う。
権利不明素材は採用せず、理由付きの棄却またはギャップとして記録する。

## Incident response

検証または安全検査が失敗した場合は、入力ファイルとエラーを保存し、原因を推測で補正しない。
`SYMLINK`、`PATH-TRAVERSAL`、`ARCHIVE`、`SECRET`、`DATA-BOUNDARY` の検出後はコミットせず、アーカイブを展開せずに隔離する。
個人メール本文、カレンダー詳細、認証情報、`PRIVATE_RAW`、`RESTRICTED` をログやIssueへ貼り付けない。

APIやナレッジベースが停止した場合は、`TIMEOUT`、`RATE_LIMIT`、`TRANSIENT` に分類して有界回数だけ再試行する。
leaseを再開し、上限到達後は `BLOCKED` または不足を明記した `COMPLETE_WITH_GAPS` に終端化する。

JSONLの破損や重複効果は行番号・タスクID・effect keyを確認する。壊れた入力を手で推測修復せず、再生成可能な正本から直す。
決定的な再現確認は次で行う。

```bash
python3 tools/chaos_check.py
```

## Model startup prompt

```text
AGENTS.md、設計仕様書、実行計画、PLANS.md、execution/task-queue.yamlをこの順で読む。
依存関係がDONEの最小IDのREADYタスクを1件claimし、受入条件を満たすまで実装する。
正常系と失敗系のテスト、security_check、validate、build_graph、evaluateを実行する。
PRIVATE_RAW、RESTRICTED、認証情報、個人データをGitへ入れず、外部送信・公開・購入・契約・削除を実行しない。
完了時はqueue、state、ExecPlanのProgress・Discoveries・Decision Log・Outcomesを更新する。
ユーザーが明示した場合だけ安全確認後にコミットとプッシュを行い、次のREADYタスクへ進む。
```
