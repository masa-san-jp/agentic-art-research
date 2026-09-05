# Agentic Art Research

証拠から制作要件までを追跡可能にする、アートリサーチ用のプロトコルと自律実行ハーネスです。

このリポジトリのAIは、調査の計画、証拠の整理、分析、判断、制作要件への翻訳、受入条件の検証、制作側への引き渡しを扱います。AIが作品を制作するリポジトリではありません。実制作物・実プロジェクト・個人の生データは、このリポジトリに保存しません。

## まず結論

- canonical repository は `config/`、`schemas/`、`tools/`、`templates/`、`tests/` と運用記録を管理します。
- 実プロジェクトは一時work rootで作業し、検証済みのプロジェクト単位で外部output rootへ出力します。
- エージェントはtaskを自分で選びません。`next_action.py` が返すtaskとcontextを、そのターンの作業入口にします。
- `PRIVATE_RAW`、`RESTRICTED`、認証情報、外部送信、公開、購入、契約、削除は扱いません。

2026-08-28の最終検証では、protocol queueの53 task、unittest 278件、validator・security・docs・chaos・graph・release gateがすべて通過しています。実行には依存関係を入れた `.venv` を使用してください。

## 兄弟リポジトリとの関係

このrepositoryは、兄弟repositoryのデータを一つにコピーする場所ではありません。各repositoryの正本をsource commit・schema・opaque referenceで参照し、境界で必要な入力またはhandoffへ変換します。

```text
self-model-notes ───────┐
art-history-notes ───────┼─ pinned reference / normalized signal ─→ agentic-art-research
marketing-trends-notes ─┘                                             │
                                                                       └─ production-handoff ─→ agentic-art-production

agentic-art-orchestration ─ cross-repository routing・pin・workspace・監査
viewer-response-notes ───── feedback / assessment（契約に適合した派生情報）
```

| repository | 役割 | 本repositoryとの関係 |
| --- | --- | --- |
| [agentic-art-orchestration](https://github.com/masa-san-jp/agentic-art-orchestration) | 複数repositoryを横断するcontrol plane | repositoryの発見、source pin、workspace、境界契約、品質状態を横断管理します。研究・制作データの正本をここへ集約しません。 |
| [self-model-notes](https://github.com/masa-san-jp/self-model-notes) | 制作者本人のself-model知識源 | 承認済みの派生signalだけを入力候補として扱います。個人の原文・`PRIVATE_RAW`・`RESTRICTED`は本repositoryへ渡しません。 |
| [art-history-notes](https://github.com/masa-san-jp/art-history-notes) | 一般美術史の知識源 | `tools/art_history_adapter.py`でsource commitを固定したread-only参照を行います。entity本文を本repositoryへ複製しません。 |
| [marketing-trends-notes](https://github.com/masa-san-jp/marketing-trends-notes) | 市場・トレンド・practiceの知識源 | freshnessと出典を持つnormalized signalとして、必要な場合に横断入力へ接続します。trend本文を本repositoryの正本にしません。 |
| [agentic-art-production](https://github.com/masa-san-jp/agentic-art-production) | 制作計画、試作、本制作、結果記録 | 本repositoryが生成するversioned production handoffを受け取る下流repoです。本repositoryはproduction repoへ直接書き込みません。 |
| [viewer-response-notes](https://github.com/masa-san-jp/viewer-response-notes) | viewer反応とassessmentの知識源 | 契約に適合したfeedback・assessmentだけを扱い、会話本文や個人情報をresearch projectへコピーしません。 |

矢印はrepository間の責任分界とversioned contractを示し、常時同期や相互の作業ツリー参照を意味しません。兄弟repositoryを使わないresearchは、該当する外部依存を省略したままofflineで実行できます。

## 60秒で動作確認する

前提は Python 3.11 以上と Git です。リポジトリのルートで実行します。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

PYTHON=".venv/bin/python"
WORK_ROOT="$(mktemp -d /tmp/agentic-art-work.XXXXXX)"
OUTPUT_ROOT="$(mktemp -d /tmp/agentic-art-output.XXXXXX)"

"$PYTHON" tools/harness.py bootstrap \
  --request tests/fixtures/harness/request.yaml \
  --protocol-root "$(pwd)" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001 \
  --now 2026-08-29T00:00:00+09:00
```

成功すると `BOOTSTRAPPED` のJSONが返り、`$WORK_ROOT/projects/harness-study/` と runtime が作られます。bootstrapはoutput rootへ出力しません。

作業開始前のtask確認はdry-runで行います。返されたJSONが、そのターンに必要なtask・context・acceptanceです。

```bash
PYTHON=".venv/bin/python"

"$PYTHON" tools/next_action.py project/harness-study \
  --worker worker-a \
  --now 2026-08-29T00:01:00+09:00 \
  --dry-run \
  --protocol-root "$(pwd)" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT"
```

実際にclaimするときは `--dry-run` を外します。同じworkerが既にtaskを持つ場合は、そのtaskが再開されます。`NO_TASK_READY` は「勝手に新しいtaskを作る」という意味ではなく、queue上に開始可能なtaskがないという終端状態です。

ハーネス自体の成功・失敗・resume・境界をofflineで確認するだけなら、reference matrixを実行します。

```bash
"$PYTHON" tools/harness_evaluate.py \
  --scenarios tests/fixtures/harness/scenarios.yaml \
  --protocol-root "$(pwd)"
```

fixtureではなく実際の依頼を使う場合は、schemaに適合するYAML/JSONを用意し、次の順で受理します。

```bash
PYTHON=".venv/bin/python"
REQUEST="./research-request.yaml"
PROJECT_ID="project/example"
WORK_ROOT="${WORK_ROOT:-$(mktemp -d /tmp/agentic-art-work.XXXXXX)}"

"$PYTHON" tools/accept_research_request.py "$REQUEST" \
  --dry-run \
  --protocol-root "$(pwd)" \
  --work-root "$WORK_ROOT"

"$PYTHON" tools/accept_research_request.py "$REQUEST" \
  --apply \
  --protocol-root "$(pwd)" \
  --work-root "$WORK_ROOT"

"$PYTHON" tools/validate.py \
  --root "$WORK_ROOT" \
  --project "$PROJECT_ID" \
  --check
```

受理されるprojectは `RESEARCH_ONLY` で始まり、`task_runtime` も初期化されます。同じ依頼の再実行は `ALREADY_APPLIED` になり、既存projectを上書きしません。

## 実行モデル

| root | 役割 | 置くもの |
| --- | --- | --- |
| protocol root | 読み取り専用の正本 | config、schema、tool、template、test |
| work root | 一時作業領域 | project、runtime、attempt workspace、journal |
| output root | 検証済み成果物 | project package、handoff、run manifest |

3つのrootを同じ場所、親子関係、symlink、広すぎるfilesystem直下にしないでください。`harness.py` はprotocolをwork rootへatomicに展開し、失敗時にpartial outputを残しません。

実運転でrequest受理から検証済みhandoffまでを一度に接続する入口は次です。`ADAPTER` には `config/worker-adapters.yaml` に定義したadapter名を指定します。

```bash
PYTHON=".venv/bin/python"
REQUEST="./research-request.yaml"
ADAPTER="your-adapter"
WORK_ROOT="$(mktemp -d /tmp/agentic-art-work.XXXXXX)"
OUTPUT_ROOT="$(mktemp -d /tmp/agentic-art-output.XXXXXX)"

"$PYTHON" tools/harness.py run \
  --request "$REQUEST" \
  --adapter "$ADAPTER" \
  --protocol-root "$(pwd)" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001 \
  --now 2026-08-29T00:00:00+09:00
```

`fake` adapterはfixture・offline検証用で、任意の研究依頼を完了させるworkerではありません。作品を制作するモデルでもありません。実providerを接続する場合は、argv形式のworker adapter、attempt request/result schema、capability、timeout、secret redactionの契約に適合させます。adapterが未設定のままでは `WORKER-ADAPTER` 系の名前付き失敗になります。

workerはattempt workspaceの許可された `write_targets` だけを書き換えます。canonical project、protocol、別project、`data/`、output root、runtime namespaceを直接変更できません。workerの変更はacceptance executorの検証後にだけpromoteされます。

中断・worker failure・human pauseからは、同じwork/output rootを指定してresumeします。

```bash
python3 tools/import_production_result.py path/to/production-result.yaml --dry-run
python3 tools/import_production_result.py path/to/production-result.yaml --apply
python3 tools/import_production_result.py path/to/production-result.yaml --apply \
  --viewer-root /path/to/viewer-response-notes
python3 tools/impact.py --production-result PR001
```

When a production result carries an explicit aggregate `viewer_response`,
`--viewer-root` is required. The importer maps only the closed
`viewer-response-record/v1` boundary and appends one record per acceptance test
to `records/viewer-response-records.jsonl`. Repeating the same result is
idempotent; existing records are never rewritten. Results without
`viewer_response` retain the legacy project-only import behavior.

`--apply`はproduction resultをruntime JSONLへ保存し、観察・試験結果をevidence candidate、逸脱・incident・変更要求をgovernance recordとして追記する。同じresult IDとhashは冪等に成功し、同じIDの異なる内容は拒否する。raw assetや禁止情報はコピーしない。詳細なsnapshot provenanceの設定は[`schemas/external/README.md`](schemas/external/README.md)を参照する。
PYTHON=".venv/bin/python"

"$PYTHON" tools/harness.py resume \
  --protocol-root "$(pwd)" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001
```

成功時だけoutput rootへ成果物をatomicに公開します。失敗時はpartial outputを作らず、同一fingerprintの再実行は `ALREADY_PUBLISHED`、内容が違う再実行は `HARNESS-PUBLISH-CONFLICT` になります。

## 実プロジェクトの保存境界

実プロジェクトの保存先は、利用環境で外部output rootとして指定します。

```text
<external-output-root>/<project-id>/
```

このリポジトリでは次を行いません。

- `projects/` に実プロジェクトをcommitする
- 実プロジェクト由来のgraphをcanonical `data/` に残す
- production input、raw evidence、個人データをコピーする
- outputを自動で外部repository、Drive、APIへ送信する

`projects/` と `profiles/` はREADME・templateの説明だけを持ちます。`data/` は正本から生成されるため手編集しません。実プロジェクトを試す場合は一時cloneまたは一時work rootで生成・検証し、検証後にプロジェクト単位で外部output rootへ移します。

## Production handoff

研究・判断・試作計画を制作側へ渡す場合だけ、projectの `manifest.yaml` を `PRODUCTION_HANDOFF` として整えます。正本は仮説、比較、要件、acceptance test、Prototype Plan、visual languageです。

一時Git worktreeでhandoffを生成する場合は、意味変更のrevisionを切り、生成物だけをcommitします。
この例では、`WORK_ROOT`をprojectのある一時Git worktree、`OUTPUT_ROOT`をcanonical repositoryの外部にある出力先として、あらかじめ設定しておきます。

```bash
PYTHON=".venv/bin/python"
PROJECT_ID="project/example"
SLUG="example"
RESEARCH_COMMIT="$(git -C "$WORK_ROOT" rev-parse HEAD)"

"$PYTHON" tools/build_handoff.py "$PROJECT_ID" \
  --root "$WORK_ROOT" \
  --protocol-root "$(pwd)" \
  --research-commit "$RESEARCH_COMMIT" \
  --handoff-id HO001 \
  --revision 1 \
  --commit

"$PYTHON" tools/validate.py \
  --root "$WORK_ROOT" \
  --project "$PROJECT_ID" \
  --check

"$PYTHON" tools/build_graph.py --root "$WORK_ROOT"
"$PYTHON" tools/bundle.py "$PROJECT_ID" \
  --audience production-agent \
  --root "$WORK_ROOT"
"$PYTHON" tools/export_handoff.py "$PROJECT_ID" \
  --root "$WORK_ROOT" \
  --protocol-root "$(pwd)" \
  --output "$OUTPUT_ROOT/${SLUG}-handoff"
```

`build_handoff.py --commit` がcommitするのは生成したhandoffだけです。ignored pathであっても対象だけをforce-addし、無関係な作業ツリー変更はcommitしません。`export_handoff.py` のoutputにはhandoff、schema snapshot、provenance、公開可能なsource-ref indexなどが入り、PRIVATE_RAW・RESTRICTED・秘密・signed URL・ローカル絶対pathは入りません。

production resultを取り込む場合は、production側が公開したschema snapshotを先に確認します。schemaの所有権やcommitが確認できない場合、dry-runも `EXTERNAL-SCHEMA` で停止します。取り込みは明示的な入力とoutputを使い、raw assetをこのrepositoryへコピーしません。

## ローカル検証

依存関係を入れた後、プロトコル全体を検証できます。

```bash
PYTHON=".venv/bin/python"

"$PYTHON" -m compileall -q tools tests
"$PYTHON" -m unittest discover -s tests -v
"$PYTHON" tools/validate.py --check
"$PYTHON" tools/security_check.py --check
"$PYTHON" tools/docs_check.py --check
"$PYTHON" tools/chaos_check.py
"$PYTHON" tools/build_graph.py --check
EVAL_ROOT="$(mktemp -d /tmp/agentic-art-evaluation.XXXXXX)"
"$PYTHON" tools/evaluate.py --offline-fixture tests/fixtures/harmony --root "$EVAL_ROOT"
"$PYTHON" tools/release_check.py \
  --offline-fixture tests/fixtures/harmony \
  --ci-evidence execution/ci-evidence.json
"$PYTHON" tools/handoff_release_check.py --require-schema-snapshot
```

システムの素の `python3` に依存関係が無い場合、`yaml` または `jsonschema` のimport errorになります。これはrepositoryの検証結果ではなく、実行環境の未セットアップです。READMEのコマンドは、セットアップ後の `.venv/bin/python` を明示して実行してください。

## 構成

```text
config/       語彙、停止、権限、保持、handoff、worker adapterの正本
schemas/      Draft 2020-12の機械可読契約
tools/        bootstrap、task入口、worker、検証、graph、bundle、handoff
templates/    新規projectとprofileの雛形
execution/    repository task queue、state、decision、CI evidence
projects/     canonicalではREADMEのみ。実projectはwork/output root
profiles/     canonicalではREADMEのみ。実profileは外部root
data/         生成物。手編集禁止
tests/        正常系・失敗系・E2E・chaos・releaseの回帰テスト
docs/         設計・運用・runtime・schema・連携の詳細
```

## 次に読むもの

- [運用手順](docs/operations.md)：導入、通常運転、resume、障害対応
- [Agent runtime guide](docs/agent-runtime-guide.md)：agentがtaskをclaimし、attemptをpromoteする手順
- [Project output boundary](docs/project-output-boundary.md)：protocol/work/outputの境界
- [Schema reference](docs/schema-reference.md)：artifactと参照関係
- [設計仕様](docs/20260811-agentic-art-research-system-design-specification.md)：全体の正本
- [実行計画](docs/20260811-agentic-art-research-repository-execution-plan.md)：実装履歴と受入条件
- [AGENTS.md](AGENTS.md)：このrepositoryで作業するagentの規則

## 安全規則

- 不明を推測で埋めず、未解決事項・gap・named failureとして残します。
- 権利不明素材は採用せず、理由付きで棄却またはgapにします。
- `data/`、runtime、handoffは正本から再生成し、手編集しません。
- 外部送信、公開、応募、購入、契約、削除は自動実行しません。

## 累積研究知識

AAK-08のowner Git保存・再読込・条件付き再利用は[research-memory](docs/research-memory.md)を参照。実project/runtimeは外部に維持し、code commitとknowledge commitを分離する。
