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

一度公開されたIDは変更しない。表示名の変更でIDを変えない。

## 参照方向

```text
question → evidence → claim → insight → decision → requirement → acceptance test
```

下流オブジェクトが上流IDを持つ。逆参照は `build_graph.py` が生成する。生成された逆参照を正本へ書き戻さない。

## 空、不明、未解決

- 値そのものが不明: `null`
- 配列に対象がない: `[]`
- 未調査: status `OPEN`
- 調査したが確定不能: status `UNRESOLVED` と理由
- アクセス不能: status `BLOCKED` と解除条件

空文字で状態を表現しない。

## JSONL

- UTF-8、1行1object。
- IDの辞書順で安定化する。
- コメントを入れない。
- 壊れた1行は、ファイル名と行番号付きで全体検証を失敗させる。

## YAML

- キー重複を禁止する。
- 日時は引用符付きRFC 3339。
- 状態と語彙は `config/vocabularies.yaml` に限定する。

