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

### Harness bootstrap

実行rootを一つにまとめず、protocolを読み取り専用のsource、workをproject/runtimeの作業領域、outputを検証済み成果物の出力先として扱う。通常の入口は次の一つである。

```bash
WORK_ROOT="$(mktemp -d)"
OUTPUT_ROOT="$(mktemp -d)"
python3 tools/harness.py bootstrap \
  --request tests/fixtures/harness/request.yaml \
  --protocol-root "$(pwd)" \
  --work-root "$WORK_ROOT" \
  --output-root "$OUTPUT_ROOT" \
  --run-id HR001 \
  --now 2026-08-25T00:00:00+09:00
```

`protocol_root`、`work_root`、`output_root`は同一root、相互の親子、symlink、filesystem/home直下の広すぎるrootを拒否する。bootstrapは一時stagingへmaterializeしてからwork rootへatomic publishするため、schema・security・runtime初期化の失敗時にwork/outputへ部分成果物を残さない。成功時もoutput rootは空のままである。

返却JSONとwork rootの`.harness/run.json`は`schemas/harness-run.schema.json`の正本である。`protocol_commit`と`protocol_tree_clean`はprotocol rootだけから取得し、work rootのdirty状態をsource provenanceとみなさない。`HARNESS-DEPENDENCY-PREFLIGHT`は、明示されたoptional dependencyのINVALID、またはmandatory dependencyのMISSING/INVALIDだけをblockingにする。

同じrequest hash・run ID・rootで再実行した場合は既存run JSONをそのまま返す。異なるrequest、別run ID、既存の非空work/output、既存projectとの衝突は`HARNESS-BOOTSTRAP-CONFLICT`で停止し、既存ファイルを上書きしない。

実行開始後のtask入口もrootを明示する。

```bash
python3 tools/next_action.py project/<project-id> \
  --worker <worker-id> \
  --now 2026-08-25T00:00:00+09:00 \
  --dry-run \
  --protocol-root <protocol-root> \
  --work-root <work-root> \
  --output-root <output-root>
```

返るacceptanceとnext stepは、protocol toolの絶対パスとwork/output rootの明示引数を持つ。`--root`は既存projectの互換shimであり、新しいharness内部のroot契約では使わない。

### Worker attempt

taskのclaim後、実行providerは`config/worker-adapters.yaml`の名前で選択し、provider固有SDKやcredentialをprotocolへ追加せず、typed requestを作ってadapterへ渡す。

```bash
python3 tools/worker_adapter.py run \
  --request <work-root>/attempts/AT001/request.json \
  --adapter fake \
  --protocol-root <protocol-root> \
  --output <work-root>/attempts/AT001/result.json
```

requestは`agent-attempt-request.schema.json`、resultは`agent-attempt-result.schema.json`で検証する。commandはargv配列で、shell interpreter、shell metacharacter、未設定capabilityを拒否する。stdoutは一つのJSON object、stderrは上限付き診断であり、timeout、exit/signal、invalid/unknown JSON、stdout/stderr超過、secret/credential、lease token、absolute pathを検出した場合は名前付き`WORKER-*` failureになる。`HUMAN_REQUIRED`はhuman decision requestとして返る。

adapterはattempt resultだけを生成し、`07_runtime/research-state.json`、`run-log.jsonl`、lease、acceptance、output rootを変更しない。結果の採用、acceptance実行、task completeは後続の原子処理が行う。結果ファイルの同一bytes再実行は冪等で、異なるbytesの既存ファイルは上書きしない。

新規プロジェクトはcanonical repositoryではなく、一時作業rootで雛形から作成し、正本ファイルを編集してから検証する。

```bash
WORK_ROOT="$(mktemp -d /tmp/agentic-art-project.XXXXXX)"
git clone --local --no-hardlinks . "$WORK_ROOT"
python3 "$WORK_ROOT/tools/new_project.py" example-project --title "Example Project" --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/task_runtime.py" project/example-project init --now 2026-08-11T00:00:00+09:00 --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/next_action.py" project/example-project \
  --worker worker-a \
  --now 2026-08-11T00:00:00+09:00 \
  --dry-run \
  --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/task_runtime.py" project/example-project claim --worker-id worker-a --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/executive_brief.py" project/example-project --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/build_graph.py" --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/bundle.py" project/example-project --audience human --root "$WORK_ROOT"
python3 "$WORK_ROOT/tools/audit.py" --root "$WORK_ROOT"
```

タスクは `execution/task-queue.yaml` の依存関係とID順に従い、1件ずつ claim する。
claim前のpreviewはread-onlyであり、previewのJSONは作業開始のcontext確認に使う。実際にclaimするlive入口はフラグなしの`next_action.py`、または互換用の`task_runtime.py ... claim`である。
停止時は進捗、実行コマンド、結果、残課題、`resume_from` を `execution/state.yaml` に残す。
期限切れleaseだけを再開し、同じ `effect_key` の効果を二重適用しない。

個人証拠や非公開音声はリポジトリへコピーせず、許可されたアダプタの不透明URI、ハッシュ、メタデータ、派生シグナルだけを扱う。
権利不明素材は採用せず、理由付きの棄却またはギャップとして記録する。

上流セッションまたは別リポジトリから依頼を受ける場合は、まずschema適合と衝突だけをdry-runで確認する。

```bash
python3 tools/accept_research_request.py path/to/research-request.yaml --dry-run --root "$WORK_ROOT"
python3 tools/accept_research_request.py path/to/research-request.yaml --apply \
  --accepted-at 2026-08-12T09:00:00+09:00 --root "$WORK_ROOT"
python3 tools/validate.py --project project/<slug> --check --root "$WORK_ROOT"
python3 tools/export_feedback_signals.py project/<slug> \
  --result-id PR001 \
  --output /tmp/agentic-art-signal-bundle \
  --root "$WORK_ROOT"
```

Knowledge records are project-local and profile instances are external. Keep real profile
data outside the protocol repository and pass its root explicitly when validating or
building the graph:

```bash
python3 tools/validate.py --root "$WORK_ROOT" --profiles-root /path/to/profile-root --check
python3 tools/build_graph.py --root "$WORK_ROOT" --profiles-root /path/to/profile-root
python3 tools/impact.py --root "$WORK_ROOT" --profiles-root /path/to/profile-root --node profile/<creator-id>::AS001
```

The profile root contains `aesthetic-signals.yaml` with a `signals` list. Each signal must
resolve every `project/<slug>::EV###` reference; no profile instance, raw personal source,
or project-derived graph is copied into this repository.

媒体判断を制作側へ渡すときは、先に`decision-log.yaml`へ媒体選択を`ADOPTED`として記録し、
その後に`05_production/visual-language.yaml`をauthoringする。validatorは媒体を自由文から
推測せず、DRAFTの空骨格以外ではDC参照、技法・要件、palette/composition、禁止表現を検査する。
production handoffのbundleには同artifactと`visual-language.schema.json`のsnapshotが含まれる。

repositoryのqueue/stateを更新した後は、実行順序のSSOT検査を必ず通す。`next_task`は、IN_PROGRESSがなければ依存完了済みの最小ID READY taskでなければならない。

```bash
python3 tools/validate.py --check
```

受理CLIは外部sourceを取得せず、production repoへ書き込まず、`RESEARCH_ONLY` projectとcanonical SHA-256 receiptだけを作る。同一依頼の再実行は `ALREADY_APPLIED` になり、異なる内容の同一IDや既存projectは拒否する。入力契約は `docs/20260812-agentic-art-research-inbound-request-extension-specification.md` にある。

feedback signal exportはimport済みのresultと監査eventをread-onlyで参照する。受入試験・要件の解決、production-result schema、hash、PII/private境界を再検証してから、明示した出力先へatomicに2ファイルを作る。外部repository、Drive、APIへの配送は行わない。

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
