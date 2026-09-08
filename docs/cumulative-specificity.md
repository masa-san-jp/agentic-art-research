# Cumulative specificity (AAK-09)

要件正本はResearch Issue94が参照するAAK-SPEC/PLAN commit `b0e7c7f8d0a1f756fa708deef4fb380a62e45e0d`。この文書はowner側の実装入口である。

外部エージェントは既存のResearch project、production-hypothesis、visual-language v1.1を作成し、criticのwrite target `04_decisions/cumulative-specificity-request.json` に候補比較の入力を置く。テーマ・候補・根拠をLLM/daemon内蔵によって自動生成することを必須にしない。既存の半決定論的制作選択とnative validationを残し、累積知識を参照する比較と根拠の監査を追加する。

```bash
.venv/bin/python tools/cumulative_specificity.py --project "$WORK_ROOT/projects/$PROJECT_ID" --request "$REQUEST" --output "$NEW_REPORT"
```

outputは新規ファイル限定。実projectとrequest/reportは常にprotocol checkoutの外に置く。requestがprojectの正規位置にあればcontext_packへ比較結果を追加し、completeは未達・未読・変更されたsnapshotをINCOMPLETEとしてproduction handoffへ流さない。legacy projectにrequestを勝手に追加しない。AAK-02の累積モードではrequestとレポートを明示して検証する。

## 入力と由来

閉じたトップレベルは `schemas/cumulative-specificity-request.schema.json`、詳細のowner/reference関係は `tools/cumulative_specificity.py` の検証を使う。

- `creator_id` とproject manifestの本人が一致すること。`origin_instance_id` は本人履歴と継承履歴の区別に使用する。
- `project_snapshot` は `project_snapshot(project)` が返すnative intake/evidence/knowledge/decision/productionファイルのSHA256 map。request/report自体は除外する。変更後は再評価が必要。
- `inputs` は id、閉じたartifact-record/v1 record、code_commit、knowledge_commit、外部payload_path。実payloadのhashをowner recordと照合する。本人signalのcreator不一致、失効・撤回・未受理、private scope不一致を拒否する。code refとknowledge refを分離する。
- `sources` はid、kind（primary/secondary/ai-derived/unknown）、locator、content_sha256、parents、input_id。出典locator/hashが対応owner artifactのsourcesに存在することを確認する。原典を読んだかどうかは外部workerが正しく記録する。ツールが出典内容の真実性や実読を推定することはない。
- `memory_query` はAAK-08の閉じたnative queryをそのまま使用し、clean code commit、creator、collection、knowledge snapshot、clockを固定する。Noneや読取失敗はUNAVAILABLE。検証可能な空storeのみEMPTY_HISTORY。検索できないことを重複LOWやPASSへ変換しない。

AI派生物からの再引用はprimary rootを増やさない。循環、解決不能な親、派生物を親に持つprimaryを拒否し、同じcontent hashを持つ別URLも同じ根と数える。primaryという記述だけで別の独立根拠を発明しない。合成fixtureは入力artifactのepistemic_statusをsimulatedとして出力へ残す。

## 候補と比較

candidateはnative hypothesis、mode、mechanism_ids、input_bindings、source_ids、knowledge_refs、continuationを持つ。hypothesisのnative schema、project内のdecision/insight、v1.1のmechanism→proposition/reference接地を検証する。input_bindingsは入力id→payload hashで、本人signalを差し替えて古い採否を再使用することを拒否する。タイトルは機構比較に使用しない。

`config/cumulative-specificity.yaml` に探索枠を固定する。reuse、unresolved-reexplore、new-referenceを別枠として扱う。reuseには実在して再利用可能な知識参照、unresolved-reexploreには未解決question参照が必要。source rootsと重複数が同じ候補だけをseedで並べ替えるため、seedを変更しても根拠・機構・適用判断は変わらない。既存self_repetitionの類似度計算と閾値を使用し、既存の検査自体を緩和しない。

AAK-08のcapture/retrieveをnative mechanismに拡張した。captureは検証済み外部projectのvisual-language techniquesをそのままcurateし、本文のコピーやタイトルの言換えから機構を推定しない。保管形式のschemaは既存visual-languageのtechniqueを参照する。履歴のsource_creatorとoriginを保ち、本人作品・継承作品・外部prior-artを区別して比較結果へ残す。

継続制作は自動禁止しない。continuationには比較先のimmutable reference、理由、実際のbefore/after mechanism、観測可能な差分試験を要求する。旧mechanismと現在のmechanismが一致したままタイトルだけ変えても差分とは認めない。意味内容や芸術的価値の最終保証を文字列比較だけで行わない。

## 結果

構造化候補、採否理由、選択id、rule hash/version、seed、input snapshot、code/knowledge ref、分類付き比較先を返す。reuse有無で根拠充足・未解決・構造重複の指標を併記する。照合しない比較では重複数をnull/NOT_SCANNEDとし、ゼロとしない。`artistic_quality_guarantee: false`。これは制作の具体性と検証可能性を支えるもので、芸術的な優劣を自動保証するものではない。

AAK09の合成受入は `tests.test_cumulative_specificity`。実エージェントによる制作・次回利用の最終受入は別途AAK02で実行する。
