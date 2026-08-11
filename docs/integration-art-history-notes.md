# art-history-notes integration

## 責任分界

`art-history-notes` は一般美術史の正本。本リポジトリは、作品プロジェクト固有の調査、個人美意識の派生シグナル、制作判断の正本。

一般美術史のentity本文を複製しない。参照時に次を持つ。

```yaml
external_reference:
  id: XR001
  system: art-history-notes
  entity_id: movement/surrealism
  uri: urn:ahn:movement/surrealism
  source_commit: "<40-char-sha>"
  acquired_at: "2026-08-11T15:00:00+09:00"
  usage: precedent_research
```

## Adapter contract

入力:

- search query、entity ID、region、centuryのいずれか
- 必要なstatus下限
- 最大件数

出力:

- source commit
- entity ID、label、status
- 主張と出典URL
- 関係するentity ID
- 未確認・hypothesisの明示

## 禁止

- `stub` を外部向け事実として引用しない。
- `draft` の未確認事項を断定しない。
- 類似contextを歴史的因果へ変換しない。
- source commitなしでcacheを正本扱いしない。

## 変更検知

同じentityのsource commitが変わったら、内容hashを比較し、関連claimとdecisionへ影響分析を走らせる。変更がない場合は再計算しない。

## Read-only adapter

adapterは`art-history-notes`のcheckoutから`data/graph.json`だけを読み、正本entity本文をこの
repositoryへコピーしない。source commitは必須で、checkoutのHEADと一致しない場合はfail closedする。

```bash
python3 tools/art_history_adapter.py \
  --root /path/to/art-history-notes \
  --source-commit 5786bb651d7b21e9a1a609f09e57b156b4591ef6 \
  --entity-id movement/surrealism \
  --minimum-status draft \
  --max-results 5
```

selectorは`entity_id`、`query`、`region`、`century`のいずれか1つ。出力はsource commit、安定
entity ID、label、status、claims、出典URL、関係を含み、`hypothesis`などの確度を変換しない。
