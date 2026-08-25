# Profile instance template

実際の制作者profileはcanonical repositoryに置かない。外部のprofile rootへ
`aesthetic-signals.yaml`を展開し、`tools/validate.py --profiles-root <path>`または
`tools/build_graph.py --profiles-root <path>`で検証・graph化する。

`signals`には`schemas/aesthetic-signal.schema.json`に適合する合成または外部管理の
派生signalだけを置く。`PRIVATE_RAW`、認証情報、個人メール本文は保存しない。
