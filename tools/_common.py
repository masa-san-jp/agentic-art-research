from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]

PROJECT_REQUIRED_FILES = (
    "manifest.yaml",
    "00_intake/creative-intent.md",
    "00_intake/constraints.yaml",
    "01_planning/question-register.yaml",
    "01_planning/research-plan.yaml",
    "02_evidence/evidence-ledger.jsonl",
    "02_evidence/source-ledger.jsonl",
    "03_knowledge/observations.jsonl",
    "03_knowledge/claims.jsonl",
    "03_knowledge/relationships.jsonl",
    "03_knowledge/contradictions.jsonl",
    "03_knowledge/external-references.jsonl",
    "04_decisions/insight-register.yaml",
    "04_decisions/decision-log.yaml",
    "04_decisions/rejected-options.yaml",
    "04_decisions/uncertainty-register.yaml",
    "05_production/creative-direction.md",
    "05_production/production-requirements.yaml",
    "05_production/acceptance-tests.yaml",
    "05_production/prototype-backlog.yaml",
    "05_production/production-agent-context.md",
    "06_governance/rights-register.yaml",
    "06_governance/privacy-review.yaml",
    "06_governance/ethics-review.md",
    "06_governance/safety-risk-register.yaml",
    "07_runtime/research-state.json",
    "07_runtime/run-log.jsonl",
    "07_runtime/change-log.jsonl",
    "07_runtime/dependency-index.json",
    "07_runtime/completion-report.json",
)

EMPTY_JSONL_FILES = tuple(path for path in PROJECT_REQUIRED_FILES if path.endswith(".jsonl"))


class DuplicateKeyError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=StrictLoader)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: JSONL record must be an object")
            records.append(value)
    return records


def iter_project_dirs(root: Path) -> Iterable[Path]:
    projects = root / "projects"
    if not projects.exists():
        return []
    return sorted(path for path in projects.iterdir() if path.is_dir() and (path / "manifest.yaml").exists())


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def yaml_list(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    value = load_yaml(path) or {}
    records = value.get(key, []) if isinstance(value, dict) else []
    if not isinstance(records, list):
        raise ValueError(f"{path}: {key} must be a list")
    return [record for record in records if isinstance(record, dict)]

