# Agentic Art Research inbound research-request 拡張仕様

- 作成日: 2026-08-12
- 版: 1.0.0
- 状態: 実装対象仕様
- 対象: `agentic-art-research` の上流セッション／リポジトリからの研究依頼受け入れ

## 1. 目的

上流のセッションまたは別リポジトリが、会話履歴やローカルパスに依存せず、research projectの受付情報を渡せるようにする。

この契約は制作実行のhandoffではない。受け入れ後のprojectは必ず `RESEARCH_ONLY` として開始し、調査・判断・制作要件化を経てから既存のproduction handoffへ進む。

```text
upstream session/repository
  → research-request.yaml/json
  → accept_research_request.py
  → project/<slug>/00_intake/
  → research workflow
  → production handoff
```

## 2. 入力契約

正本schemaは `schemas/research-request.schema.json` である。入力はUTF-8のYAMLまたはJSONで、次を含む。

- `request_id`: `RR001`形式の上流依頼ID
- `schema_version`: `1.0.0`
- `requested_at`: RFC 3339時刻
- `source`: 上流の種類・システム名・任意のrepository commitまたはopaque URI
- `project`: slug、タイトル、任意のcreator ID
- `intent`: 目的、中心の問い、利用先、観客体験、媒体・素材
- `constraints`: 期限、予算、公開範囲、許可source scope、禁止アクション、安全・権利制約
- `references`: 調査候補のラベル、opaque/public URI、権利状態
- `open_questions`: 受け入れ時点の未解決事項
- `data_boundary`: `raw_data_included: false` とGit保存可能な分類

値が不明な場合は `null`、対象がない配列は `[]` とする。個人メール本文、カレンダー詳細、音声、認証情報、ローカル絶対パスは入力に含めない。

## 3. 受け入れCLI

```bash
python3 tools/accept_research_request.py request.yaml --dry-run
python3 tools/accept_research_request.py request.yaml --apply \
  --accepted-at 2026-08-12T09:00:00+09:00
```

`--dry-run` はschema、source、データ境界、既存projectとの衝突だけを検査し、ファイルを書き込まない。`--apply` は次を一度だけmaterializeする。

```text
projects/<slug>/
├── 00_intake/research-request.yaml
├── 00_intake/research-request-receipt.yaml
├── 00_intake/creative-intent.md       # requestからの派生要約
└── 00_intake/constraints.yaml         # requestからの正規化
```

同時に `manifest.yaml` のentry pointと `research-plan.yaml` のobjectiveを更新し、通常のrepository validatorを実行する。

## 4. 冪等性と失敗

- 同じ `request_id` とcanonical SHA-256が既に受理済みなら `ALREADY_APPLIED` とし、既存projectを変更しない。
- 同じproject slugに別requestがある場合は `REQUEST-CONFLICT` で拒否する。
- 同じ `request_id` が別projectに存在する場合も拒否する。
- 既存projectがあるが受け入れreceiptがない場合は上書きせず `PROJECT-CONFLICT` で拒否する。
- schema不適合、未知フィールド、重複YAMLキー、秘密、禁止分類、絶対／traversal path、署名URLは入力ファイルと理由付きで拒否する。
- dry-runは成功しても `projects/`、`data/`、入力ファイルを変更しない。

## 5. 境界

受け入れCLIは、上流の研究依頼をproject intakeへ正規化するだけであり、次を行わない。

- 外部sourceの取得、個人データ領域へのアクセス、公開、送信、購入、契約、削除
- `PRIVATE_RAW` または `RESTRICTED` の保存・変換
- production projectの作成またはproduction repoへの書き込み
- 未指定の制作判断、リサーチ結論、制作要件の捏造

受理後に調査エージェントが参照する正本は、既存仕様どおり `00_intake`、`01_planning` 以下のproject filesである。

## 6. 完了条件

- normal requestがschema-validでdry-run/applyできる。
- apply後のprojectがrepository validatorを通過する。
- 同一requestの再applyが既存ファイルを変更しない。
- schema不適合、秘密、local path、同一IDの改変、既存project衝突がfail closedする。
- requestの原文ではなく、schemaで許可されたproject-internal intakeだけが保存される。
