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

## Repository adapter

`tools/art_history_adapter.py` は `data/graph.json` と source repository の Git HEAD だけを読む。
本文Markdownはコピーせず、プロジェクトへは `03_knowledge/external-references.jsonl` の参照メタデータだけを追加する。

```bash
python3 tools/art_history_adapter.py search \
  --source-root /path/to/art-history-notes --query surrealism --minimum-status draft
python3 tools/art_history_adapter.py import \
  --source-root /path/to/art-history-notes --target project/example \
  --entity-ids movement/surrealism --acquired-at 2026-08-11T15:00:00+09:00 \
  --usage precedent_research
```

`stub` は既定の `draft` 下限で除外され、同一 entity・commit・用途の再取込はスキップされる。
