# Theme research pass（theme-research-pass/v1）

Issue #112（P2）の実装入口。要件の正本は art-history-notes の
`docs/theme-research-cycle.md`（確定v1）§4 経路A・§5 R1/R3/R4・§6 P2、上位はAAK-SPEC v1
（knowledge-cycle / AAK-04 / AAK-06、参照commit `b0e7c7f8d0a1f756fa708deef4fb380a62e45e0d`）。
この文書は実装されたインターフェースを説明する。

## 何を埋めるか

親のknowledge cycleは、`PLAN_READY` 後に各ownerへ write job（`{action, inputs, reason}`）を求める。
art-history-notesのnative writeは `research_knowledge_intake.py`（入力: `candidate`, `source-snapshots`）
だが、その candidate を**調査して作る主体**がこれまで無く、毎runの art-history write job は事実上
`no-new-evidence` になっていた。調査はResearchの責務なので、この repository に置く。

責務境界は変えない: **調査と組み立てはResearch、保存と検証はart-history-notes、配線はorchestration。**
この tool は push・Issue・PR・code checkout への書込みを行わない。出力は常に protocol checkout の外の
新規ファイルに限る。

## 1 runの流れ（経路A）

1. **recon** — pinされた art-history checkout で owner の `tools/theme_research.py --json` を実行し
   （owner側の需要ログ `data/queries.jsonl` に検索語が残る）、`theme-research-pass/v1` を書く。
   判定は機械的: 各テーマについて `exact_label_hits` のうち `status: verified` のものがあれば covered、
   全テーマ covered なら `NO_NEW_EVIDENCE`、1つでも uncovered なら `INVESTIGATE`。
   `draft`/`stub` の主題一致は covered と見なさない。

   ```bash
   .venv/bin/python tools/theme_research_pass.py recon \
     --art-history-root "$ART_HISTORY_ROOT" --source-commit "$ART_HISTORY_COMMIT" \
     --theme "$TERM_JA" --theme "$TERM_EN" --output "$WORK/theme-pass.json"
   ```

   テーマ語は Research request の `intent.creative_question` から派生させる。語数は owner の予算
   `max_theme_terms` を超えない（超過分は owner が `truncated_themes` に返す）。

2. **判定が NO_NEW_EVIDENCE** — 調査しない。`write-job` は `no-new-evidence` を出し、`reason` に
   theme・hit_count・verified target を残す（「調べていない」と区別するため）。

3. **判定が INVESTIGATE** — 予算（pass の `budget`: candidate≤2、出典取得≤8、15分、1 run 1 pass）の中で
   出典を読む。読んだ出典は既存手順どおり §5 証拠台帳（ID・source location・SHA-256）へ記録し、
   URL→snapshot file の対応（`snapshots.json`）を作る。owner の骨格
   （`theme_research.py --emit-candidate-template`）を取り、`fill.json` に **自分で読んだ**内容だけを書く:

   ```json
   {"statement": "…固有の観測…", "consent_ref": "consent/…",
    "passages": [{"url": "https://…", "locator": "p.12", "start": 0, "end": 240}],
    "entity": null}
   ```

   `derived.json` には、この run の Research memory record（`research_memory.py commit` の receipt にある
   record identity）への参照を置く。art-history 側の `export_candidates` はこれを要求する。

4. **assemble** — 骨格＋fill＋snapshots＋derived から candidate を組み立てる。tool が計算するのは
   hash だけ（`content_sha256` / `slice_sha256` / payload hash）。statement・出典・範囲を推測しない。
   組み立て後、owner の `theme_research.py --validate-candidate` に掛け、通らなければ出力を残さない。

   ```bash
   .venv/bin/python tools/theme_research_pass.py assemble \
     --art-history-root "$ART_HISTORY_ROOT" --source-commit "$ART_HISTORY_COMMIT" \
     --pass "$WORK/theme-pass.json" --template "$WORK/template.json" --fill "$WORK/fill.json" \
     --source-snapshots "$WORK/snapshots.json" --derived-from "$WORK/derived.json" \
     --output "$WORK/candidate.json"
   ```

5. **write-job** — 親が読む owner write job を書く。

   ```bash
   # 候補がある
   .venv/bin/python tools/theme_research_pass.py write-job --pass "$WORK/theme-pass.json" \
     --candidate "$WORK/candidate.json" --source-snapshots "$WORK/snapshots.json" \
     --output "$WORK/art-history-write-job.json"
   # 予算で止まった／読んだが該当なし
   .venv/bin/python tools/theme_research_pass.py write-job --pass "$WORK/theme-pass.json" \
     --exhausted max_source_fetches --output "$WORK/art-history-write-job.json"
   .venv/bin/python tools/theme_research_pass.py write-job --pass "$WORK/theme-pass.json" \
     --no-evidence-note "read 3 sources; none describes the theme" --output "$WORK/art-history-write-job.json"
   ```

   `INVESTIGATE` なのに候補も理由も無い write job は作れない。`write` job は `verified` を含む entity を拒否する。
   commit / index / receipt 検証は親の native write が行う。

6. 次回 run では owner の `export_signals.py --knowledge-store-root … --query <theme>` が新 record を返す。
   参照しただけを再利用と数えず、採否は既存の reuse-trace/v1 に残す。

## 予算と停止

上限は owner の `config/theme-research.yaml`（theme-research-budget/v1）が正本で、recon の pass に同梱される。
到達したら止め、検証済み candidate だけを書く。上限は実 run 3回の実測を記録してから見直す。
上限を越えて調べ続けない。

## 検証

```bash
.venv/bin/python -m unittest tests.test_theme_research_pass -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/validate.py --check
.venv/bin/python tools/security_check.py --check
.venv/bin/python tools/docs_check.py --check
.venv/bin/python tools/chaos_check.py
git diff --check
```

合成 fixture は owner tool のstubで recon / validate を代替する。owner 側の本物の検証
（`validate_candidate` 通過）は art-history-notes の `tests/test_theme_research.py` が持つ。
実エージェントで1 run通す確認は AAK-02 の live acceptance の枠で別途記録する（Phase 3）。
