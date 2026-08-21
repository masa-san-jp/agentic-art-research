# Schema reference

JSON Schemaを構造の正本、本文を意味と運用規律の正本とする。競合した場合はIssueを作り、両方を同じ変更で直す。

## ID

| 対象 | 形式 |
|---|---|
| project | `project/<kebab-case>` |
| question | `Q001` |
| evidence | `EV001` |
| claim | `CL001` |
| insight | `IN001` |
| decision | `DC001` |
| requirement | `RQ001` |
| acceptance test | `AT001` |
| external reference | `XR001` |
| production hypothesis | `PH001` |
| hypothesis comparison | `HC001` |
| hypothesis uncertainty | `U001` |
| prototype plan | `PP001` |
| prototype task | `PT001` |
| production handoff | `HO001` |
| production gap | `GP001` |
| prior-art review | `PA001` |
| self-repetition review | `SR001` |

一度公開されたIDは変更しない。表示名の変更でIDを変えない。

## 参照方向

```text
question → evidence → claim → insight → decision → requirement → acceptance test
                                      └→ production hypothesis → prototype plan
production hypothesis + prototype plan + requirement + acceptance test → production handoff
```

下流オブジェクトが上流IDを持つ。逆参照は `build_graph.py` が生成する。生成された逆参照を正本へ書き戻さない。

生成グラフのノードには、表示用のプロジェクト内 `id` と、衝突を避ける `key` を持たせる。`key` は `project/<slug>::<local-id>` であり、辺の `from` / `to` はこの値を使う。影響分析で裸のIDを渡せるのはリポジトリ内で一意な場合だけで、複数プロジェクトに存在するIDは完全な `key` を指定する。

制作引き渡し拡張のschema正本は`production-hypothesis.schema.json`、`hypothesis-comparison.schema.json`、`prototype-plan.schema.json`、`production-handoff.schema.json`とする。production result schemaはproduction repoが正本であり、公開前にconsumer側で仮定義しない。

上流からresearchへ入る依頼は `schemas/research-request.schema.json`、受理記録は `schemas/research-request-receipt.schema.json` が正本である。受理後のprojectには入力のうちschemaで許可された派生情報だけを `00_intake/` に保存し、原文、秘密、ローカルパス、`PRIVATE_RAW`、`RESTRICTED` は保存しない。

`MAJOR`または`CRITICAL`の制作仮説上の不確実性は、少なくとも一つの`prototype_plan_ids`か、空でない`external_validation_reason`を持つ。handoffの`prototype_plan_ids`自体は、重大な不確実性がない場合または外部検証へ明示的に委ねる場合は空配列でよい。`open_gaps`は各項目に`blocking`を持ち、`READY` handoffではblocking gapを許可しない。

handoff hashは`tools/canonical.py`のcanonical JSON（`integrity`除外、sorted keys、compact UTF-8）から再計算し、payloadは`config/handoff-policy.yaml`の262,144 bytes上限を超えてはならない。

## Handoff bundle

`tools/export_handoff.py` が生成するproduction向けbundleは、production側がresearch作業ツリーを参照せず検証できる自己完結snapshotである。

```text
handoff-bundle/
├── manifest.yaml
├── production-handoff.yaml
├── provenance.yaml
├── schemas/
│   ├── common.schema.json
│   └── production-handoff.schema.json ほかrequest schema
└── artifacts/
    ├── production-hypotheses.yaml
    ├── hypothesis-comparison.yaml
    ├── production-requirements.yaml
    ├── acceptance-tests.yaml
    ├── prototype-plans.yaml
    ├── source-ref-index.yaml
    └── creative-direction.md
```

`manifest.yaml` はbundle内の全ファイルのraw-byte hashとfile-set hashを持つ。`source-ref-index.yaml` は `references` 配列にdecision、insight、evidenceのID、project-relative source path、`record_hash`、安全な短いsummaryを持ち、原証拠本文を複製しない。`record_hash`は元record全体のcanonical SHA-256で、ゼロ値や欠落は出力しない。

## 空、不明、未解決

- 値そのものが不明: `null`
- 配列に対象がない: `[]`
- 未調査: status `OPEN`
- 調査したが確定不能: status `UNRESOLVED` と理由
- アクセス不能: status `BLOCKED` と解除条件

空文字で状態を表現しない。

## Completion quality

`COMPLETE_WITH_GAPS`は、必要な調査を実施した後に残る非ブロッキングの未解決事項を表す。調査量または品質レビューが不足している場合は`INCOMPLETE`とし、制作handoffを生成しない。

既定の最低件数は`config/stopping-policy.yaml#defaults.completion_minimums`にある。

| 対象 | 既定の最低件数 |
|---|---:|
| evidence | 30 |
| claims | 18 |
| insights | 4 |
| decisions | 3 |
| requirements | 4 |

さらに、`04_decisions/rejected-options.yaml`、`04_decisions/uncertainty-register.yaml`、`03_knowledge/prior-art.jsonl`、`04_decisions/self-repetition-review.yaml`に各1件以上の記録を要求する。小規模な研究で最低件数を下げる場合は、`01_planning/research-plan.yaml#minimums.reason`に理由を残す。先行作品記録には作品名、出典URL、提案との差分を含め、非公開原文や作品実体は保存しない。

## JSONL

- UTF-8、1行1object。
- IDの辞書順で安定化する。
- コメントを入れない。
- 壊れた1行は、ファイル名と行番号付きで全体検証を失敗させる。

## YAML

- キー重複を禁止する。
- 日時は引用符付きRFC 3339。
- 状態と語彙は `config/vocabularies.yaml` に限定する。
