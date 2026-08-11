# Agentic Art Research

AIエージェントがアート制作のための調査を、計画、証拠収集、分析、制作判断、制作要件、試作検証まで一貫して実行するリサーチ基盤。

## 現在地

- 設計仕様: [`docs/20260811-agentic-art-research-system-design-specification.md`](docs/20260811-agentic-art-research-system-design-specification.md)
- 実行計画: [`docs/20260811-agentic-art-research-repository-execution-plan.md`](docs/20260811-agentic-art-research-repository-execution-plan.md)
- エージェント規則: [`AGENTS.md`](AGENTS.md)
- 長期計画の書式: [`PLANS.md`](PLANS.md)
- 機械可読キュー: [`execution/task-queue.yaml`](execution/task-queue.yaml)
- 運用手順: [`docs/operations.md`](docs/operations.md)
- リリース判定: [`docs/release-checklist.md`](docs/release-checklist.md)

MVP、個人証拠境界、品質評価、追加のリポジトリ安全検査、決定的chaos検査、運用文書、三回のCI相当リリースゲートを実装済み。サンプルプロジェクトは固定手順で `COMPLETE` または `COMPLETE_WITH_GAPS` に到達し、証拠から制作要件まで追跡できる。

## エージェントの開始手順

1. `AGENTS.md` を読む。
2. 設計仕様書を読む。
3. 実行計画と `execution/task-queue.yaml` を読む。
4. 依存関係が完了した最小IDの `READY` タスクを1件選ぶ。
5. 実行計画の該当マイルストーンを更新しながら実装する。
6. `python3 -m unittest discover -s tests -v` と `python3 tools/validate.py --check` を通す。
7. 状態、判断、発見、次の開始点を更新して終了する。

人間へ「次に何をしますか」と聞かず、キューの次タスクへ進む。人間確認が必要なのは、設計仕様書 §6.2 の条件だけである。

## ローカル実行

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 tools/new_project.py example-project --title "Example Project"
python3 tools/validate.py --check
python3 tools/build_graph.py
python3 tools/bundle.py project/example-project --audience human
python3 tools/audit.py
python3 tools/security_check.py --check
python3 tools/chaos_check.py
python3 tools/docs_check.py --check
python3 tools/release_check.py --offline-fixture tests/fixtures/harmony --ci-evidence execution/ci-evidence.json
python3 -m unittest discover -s tests -v
```

## 構造

```text
config/       語彙、停止、権限、保持、信頼度の正本
schemas/      機械可読スキーマ
docs/         設計、実行計画、調査・統治・連携手順
execution/    タスクキュー、進捗、判断、引継ぎ
templates/    新規プロジェクトの雛形
profiles/     制作者の時系列派生シグナル
projects/     作品単位のリサーチパッケージ
tools/        生成、検証、グラフ、バンドル、影響分析、監査
tests/        回帰テスト
data/         正本から作る生成物。手編集禁止
```

## 絶対規則

- `PRIVATE_RAW` と `RESTRICTED` をGitへ保存しない。
- 一般美術史は `art-history-notes` を正本とし、ここでは安定IDと取得commitを参照する。
- 不明を推測で埋めない。未解決のまま有限時間で終了できる。
- `data/` の生成物を手編集しない。
- 設計変更はIssueへ理由と影響を書き、仕様書と検証を同じ変更で更新する。
