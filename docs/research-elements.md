# 段階 B の要素リサーチ CLI

Issue #122 は、親の `element-request/v1` / `element-answer/v1` と
`search-request/v1` / `search-answer/v1` を使う研究単体の入口を追加する。
出所は [契約 provenance](contracts/research-elements-provenance.md) に固定する。
親 `run.py` からの接続は別 issue、作品内容を設計する段階 C も別 issue である。

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
資料判定に渡し、切り捨てない。引用は `02_evidence/excerpts.jsonl` の本文hash・文字offsetで
検証する。引用は取得本文と提示窓の両方の完全一致が必要である。

問い → 問い×方針の検索語 → 検索 → 資料の関係判定 → 抜き書き → 観察 → 主張文 →
種類 → 全主張ペアの関係 → 洞察 → 判断の問い → 選択肢1つずつ → 採用ID →
不採用理由 → 先行作品の判定 → 差分文を順に処理する。洞察には最大2主張だけを渡す。
主張の型や関係を worker が選び、支持・対立の参照と未解決の反証をプログラムが組み立てる。
資料の題名は取得メタデータから転記し、作者名やURLを生成しない。
洞察の `production_implication` は洞察文の転記であり、段階 C の作品案を代わりに生成しない。

数・方針・再試行・保存先は `config/research-elements.yaml`、検索・飽和の上限と
完了下限は `config/stopping-policy.yaml`、project の総予算は research plan に従う。
`research-plan.yaml` の任意 `element_research` で `question_count`、`search_limit`、
`options_per_decision` を明示できる。問いの数は project の `max_questions` を越えない。
下限を下げる場合は既存契約どおり `minimums.reason` が必要である。

最初の `next` に次の入力を任意で渡せる。内容は run に固定し、途中で差し替えない。

- `--history-root <external-history-dir>`: 既存の native self-repetition scanner のcreator scopeと
  閾値で主張の自己反復を計算する。入力artifactのhashを固定し、変更後の再開は拒否する。
  未指定・比較可能な履歴0件は `UNKNOWN`。段階 C の機構レビューは後続工程で行う。
- `--rights-table <external-json>`: 実際に確認済みの URL ごとの `rights_status`、
  `redistribution`、`sensitivity: PUBLIC_CITABLE` の表。未指定URLは権利不明で、
  素材採用を `REJECTED` にする。権利判定をモデルに推測させない。

`07_runtime/research-elements.json` は回答・入力・予算・台帳・生成ファイルのcheckpoint正本。
排他lock、回答のhashによる再送判定、生成途中の復旧を持つ。正本を保存してから生成するので、
中断時は `next` で生成を復旧する。入力変更・他者による生成ファイル変更は拒否する。
通常の `validate.py` はこのcheckpointがあるprojectに限り引用・台帳・回答hashとURLを追加検査する。

成功した値を捨てず、失敗した要素だけを既定5回まで再依頼する。
上限または時間予算到達は `BLOCKED` と要素名・最後の検査名を返す。
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
一時projectを削除する。オーナーprofile・段階 A の中心問い・実資料による実 run と
オーナーの出力確認は別の完了証跡であり、fake合格で代替しない。
