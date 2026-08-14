# Agentic Art Research 制作引き渡し拡張実行計画

- 作成日: 2026-08-11
- 状態: COMPLETE
- 対応仕様: `docs/20260811-agentic-art-research-production-handoff-extension-specification.md`
- 基点: `v1.0.1`以降の`main`
- 実績リリース: `v1.1.0`（production handoff拡張）

## Purpose / Big Picture

完成後は、実行エージェントが既存の証拠・判断・要件から制作仮説とPrototype Planを生成し、`agentic-art-production`が検証可能なhandoff bundleを決定的に出力できる。制作側の結果は、内容hashと出所を検証して新しい証拠候補として還流できる。

利用者は次で確認する。

```bash
python3 tools/run_project.py harmony-study --offline-fixture tests/fixtures/harmony
python3 -m unittest tests.test_feedback_import tests.test_handoff_e2e -v
python3 tools/validate.py --check
```

## Progress

- [x] (2026-08-11) `EXTENSION-DESIGN-001`: 責任境界、成果物、契約、実装フェーズを確定。
- [x] (2026-08-11 21:06 JST) `HANDOFF-SCHEMA-001`: 制作仮説、比較、Prototype Plan、handoff schema、語彙、後方互換manifest、雛形、正常・失敗fixtureを追加。70 testとvalidatorが合格。
- [x] (2026-08-11 21:40 JST) `HANDOFF-VALIDATE-001`: 仮説、Prototype DAG、handoff参照・hash・安全境界、外部schema fail-closed検査を実装。77 test、validator、docs checkが合格。
- [x] (2026-08-11 22:10 JST) `HANDOFF-BUILD-001`: handoff生成、export、production-agent bundleを実装。全84 test、validator、graph、docs check、diff checkが合格。
- [x] (2026-08-11 22:25 JST) `FEEDBACK-IMPORT-001`: production-owned schema snapshotの未公開状態をfail-closedで維持しつつ、snapshot取得後に実行できるdry-run、取込、冪等性、部分適用からの復旧、影響分析、MAJOR再開、CRITICAL人間承認待ちを実装。全90 test、validator、graph、docs check、diff checkが合格。
- [x] (2026-08-12 07:24 JST) `HANDOFF-E2E-001`: `tests/fixtures/harmony-handoff/scenario.yaml`を正本メタデータとして固定し、handoff bundleの自己完結性・改変検出、PASS/FAIL/DEVIATION/CRITICAL result、重複、hash/schema mismatch、破損JSONL、graph/impact還流、RESEARCH_ONLY後方互換性をresearch側で検証した。Production clean commit `fb15f32bf1eef0155c853c4b7c4b94df6b1bd78b`からresult schema snapshotを取得し、release gateのschema boundary probeを修正した。snapshot provenance、schema/hash compatibility、fail-closed、実schemaを使うconsumerのdry-run/apply/冪等性round-tripを含む全113 testが合格した。
- [x] (2026-08-12 07:24 JST) `HANDOFF-RELEASE-001`: 文書、CI接続済みresearch-side release gate、互換性証拠を確定し、`--require-schema-snapshot`付きgateを3回連続exit 0で確認した。検証済みhandoff拡張はmain commit `fb08b617eded3962826d913a4dd0832639d4c8c2`を対象にv1.1.0として公開済みである。
- [x] (2026-08-14 JST) `HANDOFF-REFERENCE-CONTRACT-001`: Production Issue #35/#37対応として、source-ref indexの`records`/`record_sha256`、production reference metadata、安定HTTPS URLの出力契約を追加。Research全121 testとvalidatorが合格。Production側の受入PRとは別commitで公開する。

## Surprises & Discoveries

- 2026-08-11: v1.0.1の`requirement.schema.json`は追跡性と受入試験接続を表すが、制作仮説、比較、Prototype Plan、handoffの正式schemaを持たない。
- 2026-08-11: `templates/project/05_production/prototype-backlog.yaml`は空雛形であり、代表fixtureには対応ファイルがない。既存プロジェクトを壊さない移行が必要。
- 2026-08-11: 既存状態`READY_FOR_PRODUCTION`は自然な境界だが、制作側の長期状態を追加するとresearch完了条件と混線する。handoff状態を独立させる。
- 2026-08-11: request/result schemaを片方のrepoだけで共同所有すると変更責任が曖昧になる。生成側を正本、受信側をcommit固定snapshotとする。
- 2026-08-11: production repoは設計段階で、production result schemaの正本はまだ存在しない。researchが先行して外部snapshotを捏造せず、公開後の取込を`FEEDBACK-IMPORT-001`へ移す。
- 2026-08-11: canonical hashはYAMLの表記揺れに依存させず、`integrity`を除いたhandoffをsorted-key・compact JSONのUTF-8 bytesへ正規化してSHA-256化する。正規化処理は`tools/canonical.py`へ集約した。
- 2026-08-11: READY可否はgap文面の推測ではなく、schemaの`open_gaps[].blocking`で判定する。handoff payload上限は`config/handoff-policy.yaml`の262,144 bytesとする。
- 2026-08-11: 不確実性が存在するだけではPrototype Planとの相互参照を保証できない。`PH → PP`と`PP → PH/U`の両方向を照合し、別仮説の不確実性や存在しないplanへの接続を拒否する必要がある。
- 2026-08-11: 単純な部分文字列検査は`unrestricted`を`RESTRICTED`と誤認し、空白に続く単独の`/`を絶対pathと誤認する。禁止分類はtoken境界、絶対pathはslash後の非空pathを検査する。
- 2026-08-11: H3の対象プロジェクト検証は、複数プロジェクトroot上で他プロジェクトの不備によりhandoff生成を停止させない一方、root全体の秘密・禁止拡張子・高度な安全検査は維持する必要がある。
- 2026-08-11: export先は同一bytesなら冪等成功し、異なる既存内容は`--force`なしで拒否する。dirty rootは通常拒否し、fixture等だけ`--allow-dirty`で明示的に許可する。
- 2026-08-11: production result schemaは隣接repoのclean commitにまだ存在しない。research側で仮schemaを作らず、snapshot path・provenance・raw hash・対応versionが揃わない限りimportを実行しない。
- 2026-08-11: feedback importの証拠候補は既存evidence ledgerの型へ正規化し、production result本体はruntime JSONLへ一度だけ保存する。applyは対象ファイルをsnapshotし、検証失敗時に全対象を復元する。
- 2026-08-11: production observationのgraph IDは`<result_id>/<observation_id>`を正本にする。取り込み後の再buildでも、result、observation、evidence candidate、related requirementの辺を解決できることを専用テストで固定した。
- 2026-08-11: H5の固定scenarioはresearch fixtureからhandoffを生成し、production resultはテスト時の一時rootでだけ生成する構成にした。production-owned result schemaがclean commitにまだないため、test-only schemaをrepositoryの正本やfixtureへ保存せず、実運用importのprovenance要求とfail-closed境界を維持した。
- 2026-08-12: Production result schema公開後、旧`feedback_schema_boundary` probeは欠落result入力を`EXTERNAL-SCHEMA`として期待していたため、schema snapshot存在時に`FEEDBACK-INPUT`で誤失敗した。probe内でsnapshotを一時除去してschema欠落を再現するよう修正し、実際のconsumerは変更せずfail-closed契約を維持した。
- 2026-08-14: Production consumerはsource-ref indexにProduction側で再計算できる原record本文を持たないため、Research exporterをcanonical hashの責任主体とした。categories/URLは任意metadataとしてschema化し、未提供時は推測せずProduction側のcoverage/gapで可視化する。

## Decision Log

| 日付 | 決定 | 代替案 | 理由・影響 |
|---|---|---|---|
| 2026-08-11 | researchは制作仮説・Prototype Plan・handoffまでを所有 | full production planningを同居 | WBS、予算、制作進捗の周期をresearch状態機械から分離する |
| 2026-08-11 | `workflow_mode`未指定を`RESEARCH_ONLY`として扱う | 全既存projectにhandoffを必須化 | v1 fixtureと利用契約を維持する |
| 2026-08-11 | handoff request schemaはresearchが正本 | 第三のcontract repo | 二repo段階で運用対象を増やさない |
| 2026-08-11 | production resultは証拠候補として取込 | 判断・要件を直接更新 | 実施条件と不確実性を保持する |
| 2026-08-11 | handoffのpayload hashを必須化 | commit SHAだけ | export後の内容改変を検出する |
| 2026-08-11 | production result schema snapshotはH4で取得 | H1でconsumer側が仮schemaを作る | schema生成者を正本とする責任分界を守り、存在しない上流成果物を捏造しない |
| 2026-08-11 | canonical hashは`integrity`を除くcompact sorted-key JSONのUTF-8 bytesへSHA-256を適用 | YAML textを直接hash | YAMLのインデント・key順・改行差を意味変更と誤認しないため |
| 2026-08-11 | READYのblocking gapは`open_gaps[].blocking`で明示 | gap文面のキーワード推測 | validatorの判定を機械可読な正本フィールドに固定するため |
| 2026-08-11 | handoff payload上限を262,144 bytesに設定 | 無制限 | 外部agentへ渡すデータ量とraw混入リスクをboundedにするため |
| 2026-08-11 | 仮説の不確実性とPrototype Planを相互照合 | 各方向の参照存在だけを検査 | planが別仮説や別不確実性へ誤接続した状態を成功扱いにしないため |
| 2026-08-11 | `supersedes`は現在handoffから過去handoffへの参照としてstatusと独立に扱う | `SUPERSEDED` statusだけで許可 | 後継handoffの`READY`または`ACCEPTED`状態と過去版参照を両立するため |
| 2026-08-11 | H3のbundleは正本の公開可能な制作参照、handoff、schema、provenance、manifestだけで構成 | evidence全文や実ファイルの複製 | production側の再推論を減らしつつ、raw/private境界と正本所有権を守るため |
| 2026-08-11 | handoff生成・export後の検証は対象projectへ限定し、repository-wide security scanは残す | root内全projectのvalidation結果で停止 | 複数projectの独立運用と安全検査を両立するため |
| 2026-08-11 | production result importはproduction-owned schema snapshotを前提に汎用実装する | research側でresult schemaを仮定義 | 外部契約の所有権を侵食せず、未公開schemaを成功扱いにしないため |
| 2026-08-11 | feedback importはraw assetを複製せず、URI・hash・権利区分を持つevidence candidateへ変換する | production出力をresearchへコピー | protocol repoと成果物保管境界を守り、再利用可能な出所だけを取り込むため |
| 2026-08-11 | H5のproduction result試験は一時rootへtest-only schemaを注入し、固定scenarioの入力・期待値だけをGit管理する | 未公開のproduction schemaをresearchへ複製 | オフラインE2Eを再現しつつ、result契約の正本とclean commitを捏造しないため |
| 2026-08-12 | H5のresearch-side release gateは外部schema未公開を`schema_snapshot_ready: false`として出力し、研究側の安全・互換性gateとは分離する | 未公開schemaを合格扱いにする、または全gateを停止する | 現時点の成果を継続検証しながら、production契約の未確定を隠さないため |
| 2026-08-12 | production schema snapshot CLIはcommit不一致、dirty source、schema不正、保存先逸脱、異なる既存snapshotをすべて拒否する | 取得時の入力を暗黙補正する、または既存snapshotを上書きする | 外部契約の所有権・再現性・監査可能性を守るため |
| 2026-08-12 | research-side handoff gateは通常CIで実行し、production schema snapshotの欠落・改変は`--require-schema-snapshot`でfail closedする | 外部schema待ちを理由に研究側の自動検証を無効化する | 現在確定している安全境界を継続検査し、production-owned contractの版固定を監査可能にするため |
| 2026-08-12 | result schema公開後もfeedback boundary probeはschema欠落を一時rootで再現する | 欠落result入力をschema未公開の代用にする | `FEEDBACK-INPUT`と`EXTERNAL-SCHEMA`を混同せず、snapshot有無に依存しないrelease gateにするため |
| 2026-08-14 | source-ref indexのwire keyを`records`/`record_sha256`へ固定し、production reference metadataはcanonical source recordのoptional fieldsから出力する | Production向けに別の`references`/`record_hash`形式を作る、カテゴリやURLを推測する | producer/consumerの責任を分離し、Productionが参照情報を捏造せずに受け取れるため |

## Outcomes & Retrospective

設計段階では、v1の追跡グラフを壊さずにproductionとの双方向境界を追加する方針を確定した。

`HANDOFF-SCHEMA-001`では、四つのDraft 2020-12契約、共通語彙、`workflow_mode`による後方互換manifest、六つのproject雛形、正常・失敗fixtureを追加した。既存fixtureは`workflow_mode`未指定のまま有効で、新規projectは`RESEARCH_ONLY`を明示する。production result schemaは生成側未公開のため偽造せず、所有権とfail-closed方針を`schemas/external/README.md`へ固定した。全70 test、`tools/validate.py --check`、`git diff --check`が合格した。次の開始点は`HANDOFF-VALIDATE-001`である。

`HANDOFF-VALIDATE-001`では、新schemaを既存validatorへ接続し、仮説・比較・Prototype Plan・handoffの参照、重大な不確実性の処理、Prototype DAG、選択状態、supersede/revision、必須要件の完全なsnapshot、canonical hash、機密・秘密・path・signed URL・payload上限を検査する実装を追加した。仮説・不確実性・Prototype Planは相互参照を照合し、選択ID欠落とsnapshot上の判断・試験参照改変も拒否する。production result schemaはsnapshotまたは対応版設定がない状態でfeedbackが存在した場合に`EXTERNAL-SCHEMA`で拒否し、上流契約を捏造しない。正常系と各named blocking ruleの変異fixtureを追加し、77 test、`tools/validate.py --check`、`tools/docs_check.py --check`、`git diff --check`が合格した。次の開始点は`HANDOFF-BUILD-001`である。

`HANDOFF-BUILD-001`では、canonical project sourcesからhandoffを決定的に生成する`tools/build_handoff.py`、安全な公開bundleをatomicにexportする`tools/export_handoff.py`、handoffのgraph node/edge、production-agent向けbundle、handoff起点のimpact CLIを追加した。生成時は既存handoffの意味変更をin-placeで許さず、revisionと`supersedes`を要求する。exportはschema snapshot、公開可能なsource-ref index、provenance、file manifestを含み、PRIVATE_RAW、RESTRICTED、secret、signed URL、ローカル絶対pathを拒否する。同一入力のbyte-identical再実行、異なるexport先の上書き拒否、複数project rootでの対象限定検証をテストし、84 test、`tools/validate.py --check`、`tools/build_graph.py --check`、`tools/docs_check.py --check`、`git diff --check`が合格した。次の開始点は`FEEDBACK-IMPORT-001`である。

`FEEDBACK-IMPORT-001`では、production-owned schema snapshotのpath、source repository、commit、取得日時、raw SHA-256、対応versionをすべて検証するimporterを追加した。snapshotが未公開・欠落・改変された状態ではdry-runを含めて`EXTERNAL-SCHEMA`で停止する。検証済みresultはruntime JSONLへ保存し、観察・試験結果を`EV`証拠候補、逸脱・incident・変更要求を`CR` governance recordへ変換する。same result ID/hashは`ALREADY_APPLIED`、同一IDの異なるpayloadは拒否し、MAJORは`ANALYZING`へ明示的に再開、CRITICALは人間承認待ちとして記録する。raw assetはコピーせず、graphとimpactへproduction resultの参照辺を追加した。部分適用後の再実行で監査・証拠・resultを重複させない復旧も固定した。専用6 testを含む全90 test、`tools/validate.py --check`、`tools/build_graph.py --check`、`tools/docs_check.py --check`、`git diff --check`が合格した。次の開始点は`HANDOFF-E2E-001`である。

H5のschema公開前段階では、`harmony-handoff` scenarioを使ってbundle manifestのpath・size・hash・file-set、PASS/FAIL/DEVIATION/CRITICAL result、fail-closed、後方互換性を研究側だけで検証し、production-owned schemaを捏造せずpendingとして扱った。その後の実schema取得・consumer E2E・release gate完了は次段落に記録する。

Production result schemaのclean snapshot取得後、`HANDOFF-E2E-001`のrelease gateは`schema_snapshot_ready: true`で合格した。旧boundary probeを修正し、schema snapshotを一時的に除去したprobeが`EXTERNAL-SCHEMA`でfail-closedすること、result consumerのPASS/FAIL/DEVIATION/CRITICAL round-tripがschema 1.0.0で通ることを確認した。`HANDOFF-RELEASE-001`まで完了し、v1.1.0はmain commit `fb08b617eded3962826d913a4dd0832639d4c8c2`へ公開済みである。

`HANDOFF-REFERENCE-CONTRACT-001`では、Productionが要求するsource-ref wire contractをResearch側のschema・exporter・fixtureで固定する。Productionがhashを再計算できないことを理由にゼロ値を許可せず、Research exporterがcanonical source recordから計算した非ゼロ`record_sha256`を受け渡す。完了条件はResearch側のvalidator/testとProduction側のconsumer PRを別々に確認し、どちらも人間承認前のmerge待ちとして残すことである。

## Context and Orientation

### 現行正本

- 基礎仕様: `docs/20260811-agentic-art-research-system-design-specification.md`
- 拡張仕様: `docs/20260811-agentic-art-research-production-handoff-extension-specification.md`
- 実行順序: `execution/task-queue.yaml`
- 語彙: `config/vocabularies.yaml`
- schema: `schemas/`
- 雛形: `templates/project/`
- validator: `tools/validate.py`、`tools/canonical.py` と `config/handoff-policy.yaml`
- runtime: `tools/run_project.py` と`tools/runtime/`
- traceability: `tools/build_graph.py`、`tools/impact.py`、`tools/bundle.py`

### 接続先契約

- production設計仕様: `masa-san-jp/agentic-art-production/docs/20260811-agentic-art-production-system-design-specification.md`
- handoff request schema正本: 本repo
- production result schema正本: production repo
- cross-repo検証: 各repoの固定fixtureを用い、ネットワークなしで実行する

### 実装中に守る不変条件

1. 既存v1 fixtureは変更なしで合格する。
2. `PRIVATE_RAW`、`RESTRICTED`、秘密、権利不明素材をhandoffへ出さない。
3. 既存の証拠→主張→インサイト→判断→要件→試験の辺を変更しない。
4. production側の状態を`research-state.json`へ混ぜない。
5. 外部送信、購入、契約、公開をテストやCLIが自動実行しない。
6. 生成物を正本として手編集しない。

## Plan of Work

### Milestone H1: Contract and canonical files

#### Goal

空プロジェクト、既存project、handoff projectの三種類を曖昧なく表現できる。

#### Work

1. `schemas/production-hypothesis.schema.json`を追加する。
2. `schemas/prototype-plan.schema.json`を追加する。
3. `schemas/production-handoff.schema.json`を追加する。
4. `schemas/external/README.md`へ外部schemaの所有権、snapshot要件、未公開時のfail-closed方針を記録する。
5. `project-manifest.schema.json`へ任意の`workflow_mode`を追加する。
6. ID、状態、コスト帯、期間帯、選択状態を`config/vocabularies.yaml`へ追加する。
7. `templates/project/`へ仮説、比較、Prototype Plan、handoff、変更要求、取込ログの空雛形を追加する。
8. 正常fixture、フィールド単位異常fixture、既存v1回帰fixtureを追加する。

#### Acceptance

- Draft 2020-12として全schemaが自己検証できる。
- `RESEARCH_ONLY`の既存fixtureはbyte変更なしで合格する。
- `PRODUCTION_HANDOFF` manifest fixtureは拡張entry pointがなければ`required`で失敗する。
- production result schemaが未公開であることと、公開後に必要なsource repository、commit、取得日時、SHA-256が明記されている。

### Milestone H2: Validation and lifecycle

#### Goal

不完全、不正、危険、改変済みのhandoffを外部送信前に拒否する。

#### Work

1. 新schemaを既存schema registryへ接続する。
2. 仮説→判断・インサイト、Prototype→仮説・不確実性・試験、handoff→全正本の参照を検証する。
3. Prototype taskの重複、欠落依存、循環を検査する。
4. 単一仮説理由と複数仮説比較を条件付き検証する。
5. handoff state transition、supersede関係、revisionを検証する。
6. canonical serializationとSHA-256実装を一箇所へ集約する。
7. PRIVATE_RAW、RESTRICTED、秘密、絶対path、署名付きURL、payload上限を検査する。
8. production result schema compatibilityを検査する。
9. named blocking ruleごとに正常fixtureへの単一変異テストを追加する。

#### Acceptance

- 各失敗がfile、fieldまたはline、rule、remediationを返す。
- 同じpayloadは同じhash、意味のある一文字変更は異なるhashになる。
- 非対応schema versionは利用可能版を表示して失敗する。
- `python3 tools/validate.py --check`がexit 0。
- `PRODUCTION_HANDOFF`のnamed blocking ruleが`tests/fixtures/validation-matrix.yaml`と変異テストに登録されている。

### Milestone H3: Handoff generation and export

#### Goal

正本からproductionがそのまま受理できる最小bundleを決定的に生成する。

#### Work

1. `tools/build_handoff.py`を追加し、明示timestampまたは既存metadataから決定的に生成する。
2. 推奨仮説、代替案、要件、試験、制約、gap、trigger、source refsを解決する。
3. `tools/export_handoff.py`を追加し、handoff、対応schema、provenance、manifestをbundle化する。
4. `tools/bundle.py --audience production-agent`を新成果物へ対応させる。
5. `tools/build_graph.py`へPH、PP、HO nodeと辺を追加する。
6. `tools/impact.py`へhandoff起点のupstream/downstreamを追加する。
7. 同一入力でbyte-identicalになるテストを追加する。

#### Acceptance

```text
EV001 → CL001 → IN001 → DC001 → PH001 → PP001
                                  └────→ RQ001 → AT001
PH001 + PP001 + RQ001 + AT001 ─────────→ HO001
```

がgraphとimpact CLIで確認できる。bundleには宣言外ファイル、raw evidence、ローカル絶対pathがない。

### Milestone H4: Production feedback import

#### Goal

制作結果を安全に検証し、二重適用せず、researchの証拠・影響分析へ戻せる。

#### Work

1. productionが公開したresult schemaをcommit固定snapshotとして取得し、source repository、commit、取得日時、SHA-256を記録する。
2. `tools/import_production_result.py`へ`--dry-run`と`--apply`を実装する。
3. result schema、source commit、accepted handoff ID/hash、payload hashを検証する。
4. assetを複製せずURI、hash、版、権利区分として登録する。
5. 観察と試験結果をproduction由来のevidence candidateへ変換する。
6. change request、deviation、incidentをgovernance正本へ記録する。
7. result ID/hashをeffect keyとして冪等適用する。
8. `NONE`、`MINOR`、`MAJOR`、`CRITICAL`の影響を既存state machineへ接続する。
9. crash後の再実行と同一ID異内容拒否をテストする。

#### Acceptance

- dry-runはtracked fileを変更しない。
- applyは一度だけevidence candidateと監査イベントを追加する。
- 同じresultの再applyは成功し、内容を増やさない。
- MAJORは明示的reopen event、CRITICALは人間承認待ちとなる。

### Milestone H5: Cross-repo E2E and release

#### Goal

ネットワークなしの固定fixtureでresearch→production→researchを完走し、後方互換性と安全性をrelease gateで保証する。

#### Work

1. `tests/fixtures/harmony-handoff`を追加する。
2. production repoが受理するexpected handoff bundleを固定する。
3. production repoから返るPASS、FAIL、DEVIATION、CRITICAL result fixtureを追加する。
4. source repo停止、schema mismatch、hash改変、重複result、破損JSONLのchaos testを追加する。
5. README、運用、障害対応、schema referenceを更新する。
6. release checkへhandoff、feedback、compatibilityを追加する。
7. CI相当gateを3回連続で実行し、commit SHA付きで記録する。

#### Acceptance

- 既存67件以上のtestと全新規testが合格する。
- 既存`harmony` fixtureの出力互換性を保つ。
- cross-repo fixtureのschema/hashが双方で一致する。
- release gateが3回連続exit 0。
- 外部公開は人間の明示承認後だけ行う。

## Concrete Steps

各タスク開始時に、`execution/task-queue.yaml`で依存完了済みの最小ID `READY` を一件だけ`IN_PROGRESS`へ変更する。各タスク内では次を繰り返す。

```bash
git status --short
python3 -m unittest discover -s tests -v
python3 tools/validate.py --check
python3 tools/build_graph.py
git diff --check
```

生成物差分は原因を確認し、正本変更に由来する場合だけ明示的に含める。既存の無関係なworktree変更をstageしない。

## Validation and Acceptance

全マイルストーン共通で次を満たす。

1. 正常系と失敗系がある。
2. schema、validator、fixture、文書が一致する。
3. 全IDがproject scoped graphで解決する。
4. `RESEARCH_ONLY`回帰が通る。
5. `PRODUCTION_HANDOFF`のblocking ruleが宣言的fixture matrixに載る。
6. 秘密・private境界検査がhandoff exportとfeedback importの両方向にある。
7. 終了時にProgress、Surprises、Decision Log、Outcomes、task queueを更新する。

## Idempotence and Recovery

- schemaと設定追加は既存keyを書き換えず、再適用可能にする。
- generated handoffはtemp fileへ書き、検証後にatomic replaceする。
- export先が同内容なら成功し、異内容なら`--force`なしで上書きしない。
- feedback importはresult IDとpayload hashをeffect keyにする。
- apply途中で停止した場合、監査ログから推測せず正本とeffect recordで再開する。
- external schema取得を通常テストで要求しない。snapshotを使用する。
- rollbackで既存v1成果物やユーザー生成dataを削除しない。

## Interfaces and Dependencies

### Public files

- `schemas/production-hypothesis.schema.json`
- `schemas/prototype-plan.schema.json`
- `schemas/production-handoff.schema.json`
- `schemas/external/production-result.v1.schema.json`
- `projects/<id>/04_decisions/production-hypotheses.yaml`
- `projects/<id>/04_decisions/hypothesis-comparison.yaml`
- `projects/<id>/05_production/prototype-plans.yaml`
- `projects/<id>/05_production/production-handoff.yaml`
- `projects/<id>/06_governance/production-change-requests.yaml`
- `projects/<id>/07_runtime/production-feedback-imports.jsonl`

### Public CLI

- `tools/build_handoff.py`
- `tools/export_handoff.py`
- `tools/import_production_result.py`
- 既存`validate.py`、`build_graph.py`、`bundle.py`、`impact.py`の後方互換拡張

### Dependencies

- Python 3.11以上
- 既存PyYAML、jsonschema
- hashは標準ライブラリ`hashlib`
- canonicalizationは一つの内部moduleへ集約し、複数CLIへ複製しない
- production接続は初期段階でfile bundleのみ。MCP/APIはrelease後の別計画とする

## Task Handoff Template

各タスク終了時に次を更新する。

```text
Task:
Status:
Changed canonical files:
Generated files:
Commands executed:
Results:
New blocking rules:
Surprises:
Remaining risks:
Next READY task:
Exact restart command:
```
