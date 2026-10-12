# 段階 B の要素リサーチ CLI

Issue #122 は、親の `element-request/v1` / `element-answer/v1` と
`search-request/v1` / `search-answer/v1` を使う研究単体の入口を追加する。
出所は [契約 provenance](contracts/research-elements-provenance.md) に固定する。
親 `run.py` からの接続は別 issue。作品内容を設計する段階 C は [段階 C の要素 CLI](work-elements.md)（#129）。

新規調査の既定経路は `research_elements`。`new_project.py` と依頼受理で生成する
research plan にこの値を保存し、`next_action.py` / `harness.py run` も1要素の依頼を返す。
`harness.py run --request ...` は最初の依頼までを返し、以降は下記の `answer` / `next` で進める。
新規の答え手はファイルを直接書かない。

TASK001〜009を役割単位で生成する旧経路は暫定互換用である。
research request または research plan の `research_route: legacy`、あるいは入口CLIの
`--research-route legacy` を明示した場合だけ動き、使用時に非推奨警告を標準エラーへ出す。
CLIの一時指定はplanを書き換えないため、再開時にも指定する（旧harnessの再開コマンドには含める）。
既存の受理検査・lease・production-briefはこの互換経路に残す。
段階 B の実 run が受理され、移行が完了したら旧経路を外す。

実 project は Git の外に作り、段階 A で得た中心の問い・命題を
`00_intake/creative-intent.md` に記入する。`## 現時点の中心命題` または
`## 中心の問い` があればその節を使い、なければ本文全体を使う。
命題は2000 UTF-8 bytes以内、未記入の命題は拒否する。profile の原文をコピーしない。
既存の調査成果物を持つ project への上書きは拒否する。

```bash
.venv/bin/python tools/research_elements.py next --project <external-project-dir> \
  --run-id research-001 --now <RFC3339>

.venv/bin/python tools/research_elements.py answer --project <external-project-dir> \
  --now <RFC3339> < answer.json
```

`next_action.request` がそのターンの依頼である。`next_action.kind` は親と同じ
`element`、実際の依頼種別は `request.contract_version` で判別する。
`answer` は成功なら次の依頼、検査失敗なら同じ要素の再依頼を返す。
`next` の再実行は同じ未回答依頼を返す。標準入力・標準出力は JSON のみ。
答え手はクラウドエージェントでもローカル adapter でもよく、ファイルを書かない。
CLI は provider 起動・ネットワーク検索を暗黙に実行しない。

要素の回答例:

```json
{"contract_version":"element-answer/v1","run_id":"research-001","element_id":"question.Q001","attempt":1,"value":"取得した命題についての問いは何か？"}
```

検索依頼では、利用中のエージェントが実際に取得した公開資料を上位 `limit` 件まで返す。
URL・題名・取得本文以外のメタデータを推測で加えない。取得できなければ空の結果を返す。
次は形だけの例であり、実 run に架空の本文を渡してはならない。

```json
{"contract_version":"search-answer/v1","run_id":"research-001","element_id":"search.Q001.primary","attempt":1,"results":[{"url":"<取得したHTTP(S) URL>","title":"<取得した題名>","body":"<取得した本文>"}]}
```

プログラムは全文の SHA-256 と取得時刻を `02_evidence/source-ledger.jsonl` に固定する。
同一内容の別URLは alias とし独立資料に数えない。本文は2000 bytes以下の窓に分けて
資料判定に渡す。関係する窓の抜き書きが1つ通れば、その資料の抽出を終える。
関係する窓がなければ後続の窓へ進み、全文は台帳に保持する。引用は `02_evidence/excerpts.jsonl` の本文hash・文字offsetで
検証する。引用は取得本文と提示窓の両方の完全一致、20文字以上、
問いの内容語を1つ以上含むか取得本文の完全な1文であることが必要である。
完全な1文は本文の開始または文末記号の直後から始まり、文末記号で終わる連続部分とする。
内容語は機械的な字句照合であり、意味的関連性を保証しない。
観察と抜き書き、主張と観察の類似度が0.9以上なら、その要素だけを再依頼する。

問い → 問い×方針の検索語 → 検索 → 資料の関係判定 → 抜き書き → 観察 → 主張文 →
種類 → 対象主張ペアの関係 → 洞察 → 判断の問い → 選択肢1つずつ → 採用ID →
不採用理由 → 先行作品の判定 → 差分文を順に処理する。洞察には最大2主張だけを渡す。
主張の型や関係を worker が選び、支持・対立の参照と未解決の反証をプログラムが組み立てる。
資料の題名は取得メタデータから転記し、作者名やURLを生成しない。
洞察の `production_implication` は洞察文の転記であり、段階 C の作品案を代わりに生成しない。
検索語にはprimary（一次資料）、counterexample（反例）、independent-secondary（独立した二次資料）の
方針説明を1文ずつ渡す。判断の問い・選択肢・不採用理由には中心命題を渡し、判断の問いには
元の研究の問いも渡す。先行作品判定の抜き書きは本文の完全一致で重複を除く。

主張ペアは同じ問いの中の全組を優先し、問いをまたぐ組は同じ資料を根拠に持つ場合だけ列挙する。
プログラムが順序と対象を決め、`max_claim_pairs`（既定120組）で打ち切る。
`completion.pair_review` に上限・選択数・省略の有無を残し、省略は完了時のgapとして扱う。

数・方針・再試行・保存先は `config/research-elements.yaml`、検索・飽和の上限と
完了下限は `config/stopping-policy.yaml`、project の総予算は research plan に従う。
`research-plan.yaml` の任意 `element_research` で `question_count`、`search_limit`、
`options_per_decision` を明示できる。問いの数は project の `max_questions` を越えない。
下限を下げる場合は既存契約どおり `minimums.reason` が必要である。
今回の検査変更で要素policyはversion 2となる。version 1を固定したcheckpointは新コードで
再開せず、元の固定commitで完了させるか、新しい外部projectで始める。回答や検査の黙った移行はしない。

最初の `next` に次の入力を任意で渡せる。内容は run に固定し、途中で差し替えない。

- `--history-root <external-history-dir>`: 既存の native self-repetition scanner のcreator scopeと
  閾値で主張の自己反復を計算する。入力artifactのhashを固定し、変更後の再開は拒否する。
  未指定・比較可能な履歴0件は `UNKNOWN`。段階 C の機構レビューは後続工程で行う。
  作品カタログは[過去作品adapter](#過去作品の記録を-history-root-に変換するissue-133)で外部履歴へ変換する。
- `--rights-table <external-json>`: 実際に確認済みの URL ごとの `rights_status`、
  `redistribution`、`sensitivity: PUBLIC_CITABLE` の表。未指定URLは権利不明で、
  素材採用を `REJECTED` にする。権利判定をモデルに推測させない。

`07_runtime/research-elements.json` は回答・入力・予算・台帳・生成ファイルのcheckpoint正本。
排他lock、回答のhashによる再送判定、生成途中の復旧を持つ。正本を保存してから生成するので、
中断時は `next` で生成を復旧する。入力変更・他者による生成ファイル変更は拒否する。
通常の `validate.py` はこのcheckpointがあるprojectに限り引用・台帳・回答hashとURLを追加検査する。

engineはGit配下のprojectを拒否するため、protocol repositoryだけの `validate.py --check` は
実projectの要素検査を実行しない。外部work rootの `projects/<slug>` を対象に、次を実行する。
同じ検証で従来のschema・追跡関係・安全検査と要素checkpointの検査を行い、失敗時はexit 1となる。
`--check` は検証対象を変更しない。

```bash
.venv/bin/python tools/validate.py --check \
  --protocol-root <protocol-dir> --work-root <external-work-root> \
  --project project/<slug>
```

成功した値を捨てず、失敗した要素だけを既定5回まで再依頼する。
資料の窓ごとの要素（関係判定 `relevance.*`・抜き書き `excerpt.*`）が上限に達した場合は、その窓を根拠なしとして飛ばし、checkpoint の `abandoned` に最後の検査結果を残して次へ進む。主張 `claim.*` が上限に達した場合も、その観察から主張を作らずに次へ進む。主張の組 `pair.*` が上限に達した場合は、その組を関係なしとして次へ進む。
設定の `dedupe_identical_quotes` が真なら、同じ文の抜き書き（別の URL・別の問いから得たもの）からは観察と主張を1回だけ作る。`relevance_with_proposition` が真なら、関係判定に中心命題も渡す。どちらも新しい run の既定で、設定は checkpoint に固定されるため、それより前に始めた run の要素 ID は変わらない。
`art_reference_search` が真で、`creative-intent.md` の参照候補に `art-history:` の参照があれば、各問いはまず `art-reference` の検索を1回行う。検索語には芸術の参照（例: conceptual art）をそのまま含め、費用・お金・生活の語は入れない。芸術の語と費用の語をひとつの検索語にまとめると、お金の記事しか返らなかったため（orchestration #293）。それ以外の要素が上限に達した場合、または時間予算到達は `BLOCKED` と要素名・最後の検査名を返す。
探索を `SEARCH_ATTEMPT`、`SOURCE_REVIEWED`、`ANSWER_FOUND`、`EVIDENCE_ROUND` に記録し、
飽和・戦略数・資料上限で問いを理由付き `UNRESOLVED` に終端化する。
不正な envelope、別のID・試行番号は exit 2 で拒否し、試行を消費しない。
`BLOCKED` は exit 1、それ以外の正常処理は exit 0。

`status: COMPLETED` は段階 B の要素依頼が終わったことだけを表す。
`completion` には既存の全調査下限・必須reviewによる `INCOMPLETE` / `COMPLETE_WITH_GAPS` /
`COMPLETE` と未解決事項を出す。`project_completed: false` のままで、legacy task leaseや
`07_runtime/completion-report.json` を完了扱いにしない。Productionの要件、production-brief、
権利・安全レビュー、native acceptance/completion は従来の工程へ引き継ぐ。
全projectを完成させる gate を要素検査で黙って外さない。

fakeの資格確認は `tests/test_research_elements.py`。架空資料は `example.invalid` を使い、
一時projectを削除する。既定の4問を変更せず、要素IDごとに異なる決定的な問い・検索語で
118依頼（要素106、検索12）を全て初回回答で通す。資料は重複除去後2件なので、
既定の調査下限を満たす実 run とは異なり、native qualityは `INCOMPLETE` のままである。
依頼数は検索結果・関係判定・予算によって変わり、118は同梱fakeの検証値である。
オーナーprofile・段階 A の中心問い・実資料による実 run と
オーナーの出力確認は別の完了証跡であり、fake合格で代替しない。

## 過去作品・制作提案の記録を history-root に変換する（Issue #133）

カタログのroot（`works/index.yaml`または`plans/index.yaml`を含むディレクトリ）を明示して、
決定論的adapterを実行する。モデル・networkは使わず、入力は読み取り専用。

```bash
.venv/bin/python tools/works_history.py --works-root <catalog-root> --output <external-history-dir>
.venv/bin/python tools/research_elements.py next --project <external-project-dir> --history-root <external-history-dir>
```

indexの`version: 1`、`records`内の`id: W0001`、`path: works/W0001-short-slug`を読み、
列挙された作品だけを変換する。`creator_id`はindex、`metadata.yaml`または`lineage.json`の
明示値を使う。既定では欠落、`unknown`、宣言間の不一致はエラー。
利用者がカタログ全体を同一creatorの履歴と明示した場合だけ、`--creator-id <owner-scope>`で
works/plans共通の比較scopeを指定できる。欠落identityもこの明示scopeで扱い、元の
creator（欠落ならnull）はreceiptのsource_creator_idへ残す。元の宣言間の不一致や
unknownは、この指定でも拒否する。candidateにも同じcreator IDを使う。候補projectがcreatorを宣言して
いれば、その値と一致する作品だけが比較される。creator未宣言の候補はnative scannerの
既存動作どおり全creatorと比較されるため、本人の比較には一致するIDを設定する。

各作品の`README.md`と`record.md`（少なくとも一方が必要）の本文段落を、原文のまま
`03_knowledge/claims.jsonl`の`statement`へ写す。これは出典付き抜粋で、推論で生成した
主張ではない。`05_production/creative-direction.md`には`record.md`を丸ごと写し、
無ければREADMEを使う。`manifest.yaml`には安定した作品project IDとcreatorを記録する。
worksのmetadataはidentityにだけ使う。plansは過去の提案として比較し、完成作品とはみなさない。
`plans/index.yaml`のversion 1 / records、`id: P0001`、`path: plans/P0001-short-slug`から
1 planにつき1履歴projectを作る。同じcreator検査を適用する。比較本文は`plan.md`の
原文段落（表形式のplanでは比較用の行・列の本文値のみ）と`metadata.yaml`の明示的な比較テキストfield（native scannerのTEXT_FIELDS、
例: title / statement / mechanism）の値のみ。creative-directionには同じ抜粋を重複除去して写す。
表の見出し・field名・管理用の行・定型文は、どちらの比較artifactにも入れない。
README、lineage、indexの表示値、mediaは比較文にしない。lineageはidentityにのみ使う。
片方の本文sourceだけでも比較テキストがあれば受理し、内容が無ければエラーにする。
タイトルから機構や制作命題を推測しない。
履歴はscanner向けの最小入力であり、通常のresearch project全体のschemaは満たさない。

`works-history.json`に履歴project数・work_idまたはplan_id・creator・相対出典パス・入力SHA-256を保存する。
実作品本文・履歴・receiptはGit外に置く。symlink、パス逸脱、Git配下への出力、入力との
重なりは拒否する。同じ入力・出力の再実行は無変更で、変更済み入力や非空の異なる出力は
上書きしない。更新時は新しい履歴ディレクトリを使う。最初の`next`がmanifestとscanner
artifactのhashを固定するため、進行中のrunの履歴は変更できない。

空のworks indexでもplans indexの提案を取り込む。両indexが空なら空の履歴となる。
未登録の作品・planやmediaは採用しない。比較可能な履歴が1件以上あればStage Bは
測定結果から`SR001`を記録し、類似が無くても履歴hashを比較根拠にする。0件なら
`UNKNOWN`・review 0件を維持し、`QUALITY-REQUIRED-SELF_REPETITION_REVIEW`は解消しない。
段階Cの機構レビューは、引き続きその工程のnative scannerで別途行う。
