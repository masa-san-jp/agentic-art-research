# 段階 C の要素 CLI（作品の中身）

Issue #129。段階 B（[要素リサーチ](research-elements.md)）が `COMPLETED` になった project で、
調査の結果から作品の中身を作る。仕組みは段階 B と同じで、`element-request/v1` を1件ずつ出し、
`element-answer/v1` を1件受け取り、プログラムで確かめ、落ちた要素だけを既定5回までやり直す。
設計の正本は orchestration の `docs/20261007-element-harness-design.md` 6章。

```bash
.venv/bin/python tools/work_elements.py next --project <external-project-dir> --run-id work-001 --now <RFC3339>
.venv/bin/python tools/work_elements.py answer --project <external-project-dir> --now <RFC3339> < answer.json
```

`next_action.py` は、段階 B が `COMPLETED` で判断が1件以上あれば、続けて段階 C の依頼を返す。
答え手はファイルを書かない。CLI はモデルもネットワークも呼ばない。

## 入力と所有するファイル

- 入力は段階 B の checkpoint（`07_runtime/research-elements.json`）。開始時にその hash を固定し、
  段階 B の入力・資料の hash の検査（段階 B の `read`）も毎回通す。変わったら再開を拒否する。
- 段階 C の checkpoint は `07_runtime/work-elements.json`。段階 B が持つファイルには書かない。
- 書くファイル: `04_decisions/production-hypotheses.yaml`、`04_decisions/hypothesis-comparison.yaml`、
  `05_production/production-requirements.yaml`、`acceptance-tests.yaml`、`prototype-plans.yaml`、
  `creative-direction.md`、`production-brief.yaml`。テンプレートのままでなければ上書きを拒否する。
- ファイルは全要素が通ってから組み立てる。`BLOCKED` の run はテンプレートを残す。

## 推論（1要素1推論）

1. 作品案（`plan.PH00n.*`）: 題、命題、核の問い、体験、構成要素（1つずつ）、鑑賞者の行為、
   素材と振る舞い、飛躍、入れないもの、先行作品との違い、作り方の段取り（1つずつ。各段取りの
   完了条件と影響の種類も1つずつ）、技術の見通し、費用・期間・会場依存（選択肢）、試作で確かめること。
2. 推薦案の要点（`point.PH00n.*`）: 出会いの3場面（1つずつ）、位置、最初の数秒、30秒後、3分後、
   分野、対抗するもの、主張、反対する人、否定するもの、語らずに見せるもの、仕掛け、
   「技術を外すと成り立たないか」（はい/いいえ＋理由）。
3. 試作（`prototype.PH00n.*`）: 試作の問い、方法。
4. 制作要件（`requirement.PH00n.<種類>`）と受け入れテスト（`acceptance.RQ00n.*`）:
   体験・素材・行為・仕掛けの4種を1件ずつ。合格条件1文と方法（選択肢）。

「技術を外しても地味になるだけ」（いいえ）と判定されたら、仕掛けの要素だけを
`point.PH00n.mechanism.<回>` として再依頼し、もう一度判定する（`mechanism_rounds`、既定3回）。
案と他の要点は捨てない。最後まで `MERELY_PLAINER` なら、その判定のまま brief に書き、
制作側（agentic-art-production の `build_plan.py`）の blocking gap になる。

## プログラム

- 案に使う判断の選び方: 段階 B の採用済み判断を、根拠の数・洞察の主張の数の順に並べ、
  採用案が重複しないものから `plan_count`（既定2）件。
- 作品案の比較と推薦: 根拠の厚さ（根拠と主張の数）と実現性（費用・期間・会場依存）を数え、
  合計の高い案を推薦する（同点は番号の若い案）。対立する主張の有無も比較の軸に書く。
- 転記: なぜ今か（取得日と推薦案の洞察）、先行作品（段階 B の prior-art）、自己反復の評価
  （段階 B の scan。履歴が無ければ `UNKNOWN` と明記）、調べた問い、読んだ資料の数と題、
  出てきたこと（主張・洞察・判断の数と洞察）、未決事項（未解決の問い、対立、下限未達、未実施の試作）。
- 要件はすべて `mandatory` で、受け入れテスト（`result: NOT_RUN`）を1件ずつ持つ。
  試作計画は推薦案について1件。推薦されなかった案の不確実性には、試作しない理由を書く。
- 「未記入」「未確定」「∩」「無し」は全要素で拒否する。転記した値も、空やテンプレートの
  文言だけの欄があれば書かない。

## 飛ばせる要素と止まる要素

- 2案目以降の要素と「入れないもの」は、上限に達したら `abandoned` に記録して先へ進む
  （その案は比較から外し、1案なら `single_hypothesis_rationale` を書く）。
- それ以外（1案目、要点、試作、要件）が上限に達したら `BLOCKED` で止まり、要素名と最後の検査を返す。

## 制作への受け渡しの準備（#134）

- 参照の分類（`reference.<カテゴリ>`）: 制作側が URL を求める CONCEPT / VISUAL / METHOD ごとに、推薦案の根拠資料
  （判断の根拠と、洞察の主張の根拠）から当たるものを1つ選ぶ。当たるものが無ければ `NONE`。飛ばせる要素で、
  上限に達したらそのカテゴリは未対応のまま（制作側の参照の gap になる）。結果は
  `05_production/reference-categories.yaml` に書く。
- デジタル試作の計画（`PP002`）: プログラムが書く。制作プランの寸法・素材・数量を読んで構成要素を SVG に描く
  2つの段取り（READ_ONLY → REPOSITORY_WRITE）で、物の制作・購入・公開・依頼はしない。制作側は handoff に
  この種の計画を求める。
- `creative-direction.md` の「完了経路」: 推薦案の段取りを順に番号付きで書く（プログラム）。制作側はこれを
  計画の作業に結びつける。

## 段階 D を始めてよい条件（#134）

`work_elements.stage_d_start()`（CLI: `tools/work_elements.py stage-d-start --project <dir>`）が判定する。
次をすべて満たすときだけ、制作プラン（段階 D、agentic-art-production `tools/plan_elements.py`）を始めてよい。

1. 段階 B と段階 C の checkpoint がどちらも `COMPLETED`（`BLOCKED` ではない）で、段階 C の出力ファイルが
   checkpoint と一致する。
2. 制作要件の数が完了の下限（`minimums.requirements`、1以上）以上で、必須要件はすべて受け入れテストを持つ。
3. 仕掛けの判定が `CEASES_TO_WORK`。`MERELY_PLAINER` のままなら段階 C の仕掛けを直すまで始めない。
4. `complete.py` の中核の検査（manifest、問いの終端、根拠台帳、主張の参照、判断の追跡、要件のテスト可能性、
   私的データなし、完了報告）がすべて通る。

数の下限（根拠・主張など）、自己反復レビュー、権利・プライバシーのレビュー、独立した資料の不足は段階 D を
止めない。止めずに、handoff の open gap（`blocking: false`、担当 research）として制作へ渡す。制作側の段階 D は
gap ごとに確かめ方と解消の条件を聞く。調査は `INCOMPLETE` のままで、完了扱いにはしない（`project_completed: false`）。
小さく絞った実 run（問い2つなど）では数の下限に届かないことが多く、それを理由に制作プランの検討まで止めると、
要件と受け入れテストがそろっているのに先へ進めないため。

`build_handoff.py` / `export_handoff.py` は、段階 C の checkpoint がある project ではこの判定を使う。
条件を満たせば manifest（段階 B の checkpoint が持つファイル）を書き換えずに handoff を作り、満たさなければ
`STAGE_D_NOT_READY: <理由>` で止まる。段階 C の checkpoint が無い project は従来どおり
`workflow_mode: PRODUCTION_HANDOFF` と `complete.py` の完了を求める。

## 完了の意味

`completion.stage: C` は段階 B の数に要件の数を足して完了の下限を数え直す。
`project_completed: false` のままで、権利・プライバシーのレビューは従来の工程に残す。段階 D は上の条件で始める。
`validate.py --check` は段階 C の checkpoint と生成ファイルの一致も検査する（`WORK-ELEMENT-*`）。

fake の資格確認は `tests/test_work_elements.py`（段階 B → C を要素単位で最後まで通す）。
fake の合格は実 run の確認の代わりにならない。
