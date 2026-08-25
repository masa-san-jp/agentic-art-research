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

MVP、個人証拠境界、品質評価、追加のリポジトリ安全検査、決定的chaos検査、運用文書、三回のCI相当リリースゲートを実装済み。固定fixtureで、実プロジェクトを保存せずに証拠から制作要件までの追跡を検証できる。

## 成果物の保存境界

このリポジトリはプロトコル専用であり、デモや実際の生成物を常設しない。実際のアートリサーチ成果物は、次の外部フォルダにプロジェクト単位で保存する。

/Users/masa/マイドライブ/AI-Agent-Pipeline/Agentic-Art-Output/<project-id>/

生成と検証は一時cloneまたは一時作業rootで行い、検証後に projects/<project-id>/ だけを外部出力先へコピーする。canonical repositoryへ projects/の実データや、それに由来する data/のgraphを追加しない。

## エージェントの開始手順

1. `AGENTS.md` を読む。
2. 設計仕様書を読む。
3. 実行計画と `execution/task-queue.yaml` を読む。
4. 依存関係が完了した最小IDの `READY` タスクを1件選ぶ。
5. 実行計画の該当マイルストーンを更新しながら実装する。
6. `python3 -m unittest discover -s tests -v` と `python3 tools/validate.py --check` を通す。
7. 状態、判断、発見、次の開始点を更新して終了する。

人間へ「次に何をしますか」と聞かず、キューの次タスクへ進む。人間確認が必要なのは、設計仕様書 §6.2 の条件だけである。

## 上流セッションからの研究依頼

別セッションまたは別リポジトリからは、会話の貼り付けではなく、`schemas/research-request.schema.json` に適合するYAML/JSONを渡す。受け入れはdry-runで確認してからapplyする。

```bash
python3 tools/accept_research_request.py path/to/research-request.yaml --dry-run --root "$WORK_ROOT"
python3 tools/accept_research_request.py path/to/research-request.yaml --apply --root "$WORK_ROOT"
python3 tools/validate.py --project project/<slug> --check --root "$WORK_ROOT"
```

受理されたprojectは必ず `RESEARCH_ONLY` で開始する。同じ依頼の再実行はreceiptのcanonical SHA-256で `ALREADY_APPLIED` になり、既存projectを変更しない。productionへの引き渡しは、研究・判断・試作計画を完了した後に「制作引き渡し（PRODUCTION_HANDOFF）」の手順へ進む。入力契約と拒否境界の詳細は `docs/20260812-agentic-art-research-inbound-request-extension-specification.md` を参照する。

## ローカル実行

プロトコル自体の検証は、このリポジトリで実行する。

~~~bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 tools/validate.py --check
python3 tools/build_graph.py --check
python3 -m unittest discover -s tests -v
~~~

判断、typed registry、制作要件を人間向けに確認するbriefは、一時作業rootで次のように生成する。

~~~bash
python3 tools/executive_brief.py project/<slug> --root <temporary-work-root>
~~~

次の実行taskを変更なしで確認する場合は、明示的にdry-runを指定する。

~~~bash
python3 tools/next_action.py project/<project-id> \
  --worker <worker-id> \
  --now 2026-08-25T00:00:00+09:00 \
  --dry-run \
  --root <temporary-work-root>
~~~

`TASK_PREVIEWED`は未claim、`TASK_RESUME_PREVIEW`は既存leaseの再開previewであり、どちらもprojectのstate/logを変更しない。queue/stateの整合性は`python3 tools/validate.py --check`で検査する。

## 制作引き渡し（PRODUCTION_HANDOFF）

制作引き渡しを使うプロジェクトでは、`manifest.yaml` の `workflow_mode` を `PRODUCTION_HANDOFF` にし、仮説、比較、Prototype Plan、要件、受入試験を正本として整える。次でhandoffを決定的に生成・検証・exportできる。

```bash
python3 tools/build_handoff.py project/<project-id>
python3 tools/validate.py --project <project-id> --check
python3 tools/build_graph.py
python3 tools/impact.py --handoff HO001
python3 tools/bundle.py project/<project-id> --audience production-agent
# review and commit the generated canonical handoff before a clean export
python3 tools/export_handoff.py project/<project-id> --output data/handoffs/<project-id>
```

export bundleは `manifest.yaml`、`production-handoff.yaml`、`provenance.yaml`、schema snapshot、制作入力のsnapshot、匿名化されたsource-ref index、creative directionだけを含む。原証拠本文、PRIVATE_RAW、RESTRICTED、ローカル絶対パスは含めない。`data/handoffs/` は再生成物であり、手編集しない。

fixtureや未commit作業ツリーを検証するときだけ、exportに `--allow-dirty` を明示する。この場合、provenanceの `source_tree_clean` は `false` になる。

実プロジェクトを試す場合は、一時cloneを作り、検証後に外部出力先へプロジェクト単位でコピーする。

~~~bash
PROJECT_OUTPUT_ROOT="/Users/masa/マイドライブ/AI-Agent-Pipeline/Agentic-Art-Output"
WORK_ROOT="$(mktemp -d /tmp/agentic-art-project.XXXXXX)"
git clone --local --no-hardlinks . "$WORK_ROOT"
python3 "$WORK_ROOT/tools/new_project.py" example-project --title "Example Project" --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/executive_brief.py" project/example-project --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/validate.py" --root "$WORK_ROOT" --check
cp -R "$WORK_ROOT/projects/example-project" "$PROJECT_OUTPUT_ROOT/example-project"
~~~

### 制作結果の還流

production resultのschemaは`agentic-art-production`が正本として公開したものだけを使う。schema snapshot、source commit、取得日時、SHA-256、対応versionがpolicyに揃わない間は、dry-runを含むimportが`EXTERNAL-SCHEMA`で停止する。

snapshot取得後は、まず変更なしで確認し、承認可能な結果だけを適用する。

```bash
python3 tools/import_production_result.py path/to/production-result.yaml --dry-run
python3 tools/import_production_result.py path/to/production-result.yaml --apply
python3 tools/impact.py --production-result PR001
```

`--apply`はproduction resultをruntime JSONLへ保存し、観察・試験結果をevidence candidate、逸脱・incident・変更要求をgovernance recordとして追記する。同じresult IDとhashは冪等に成功し、同じIDの異なる内容は拒否する。raw assetや禁止情報はコピーしない。詳細なsnapshot provenanceの設定は[`schemas/external/README.md`](schemas/external/README.md)を参照する。

import済みresultから次回researchが再利用できる匿名化済みsignal bundleを、外部送信なしで明示出力できる。

~~~bash
python3 tools/export_feedback_signals.py project/<slug> \
  --result-id PR001 \
  --output <signal-bundle-directory> \
  --root "$WORK_ROOT"
~~~

出力は`manifest.json`と`signals.jsonl`だけで、同じ内容の再実行は`ALREADY_EXPORTED`、異なる既存bundleは`FEEDBACK-EXPORT-CONFLICT`で拒否する。`projects/`と`data/`への暗黙出力、原asset、識別情報、private URL、未知fieldの補正は行わない。

## 構造

```text
config/       語彙、停止、権限、保持、信頼度の正本
schemas/      機械可読スキーマ
docs/         設計、実行計画、調査・統治・連携手順
execution/    タスクキュー、進捗、判断、引継ぎ
templates/    新規プロジェクトの雛形
profiles/     protocol READMEのみ。実profileは外部rootを--profiles-rootで指定
projects/     一時作業rootでmaterializeする作品単位のリサーチパッケージ。canonical repositoryには実データを置かない
tools/        生成、検証、グラフ、バンドル、影響分析、監査
tests/        回帰テスト
data/         正本から作る生成物。手編集禁止
```

`05_production/visual-language.yaml`は、ADOPTEDな媒体decisionから制作側へ渡す
媒体・技法・色・構成・禁止表現の正本である。DRAFTでは空骨格を許可し、
`READY_FOR_PRODUCTION`以降は`tools/validate.py`が完全性と参照をblocking検査する。
素材仕様、施工図、製造パラメータ、権利・安全の最終保証はproduction/governance側の責務である。

## 絶対規則

- `PRIVATE_RAW` と `RESTRICTED` をGitへ保存しない。
- 一般美術史は `art-history-notes` を正本とし、ここでは安定IDと取得commitを参照する。
- 不明を推測で埋めない。未解決のまま有限時間で終了できる。
- `data/` の生成物を手編集しない。
- 設計変更はIssueへ理由と影響を書き、仕様書と検証を同じ変更で更新する。
