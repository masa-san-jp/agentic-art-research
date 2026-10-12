#!/usr/bin/env python3
"""Deterministically project indexed work and proposal text into external scanner history.

History projects are minimal scanner inputs, not schema-valid research projects.
No model calls, inferred propositions, media reads, or source writes are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

import yaml
from _common import DuplicateKeyError, InputParseError, StrictLoader
from self_repetition import TEXT_FIELDS


class WorksHistoryError(ValueError):
    """An indexed source or output boundary is invalid."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n'


def _real(path: Path) -> Path:
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise WorksHistoryError(f'{path}: symlinks are not supported')
    return path.resolve()


def _mapping(value, path):
    if not isinstance(value, dict):
        raise WorksHistoryError(f'{path}: expected a mapping')
    return value


def build_history(works_root: Path, output: Path, *, creator_id: str | None = None) -> dict:
    """Validate all sources, then atomically publish an immutable history tree."""
    if creator_id is not None and (not isinstance(creator_id, str) or not creator_id.strip() or creator_id == 'unknown'):
        raise WorksHistoryError('explicit creator_id scope must be nonempty and known')
    root, output = _real(works_root), _real(output)
    if not root.is_dir():
        raise WorksHistoryError(f'{root}: --works-root must be a catalog directory')
    if root == output or root in output.parents or output in root.parents:
        raise WorksHistoryError('output and works root must not overlap')
    if any((parent / '.git').exists() for parent in (output, *output.parents)):
        raise WorksHistoryError(f'{output}: output must be outside Git')
    files: dict[str, str] = {}
    hashes: dict[str, str] = {}

    def read(relative):
        path = root / relative
        if _real(path) != path or not path.is_file():
            raise WorksHistoryError(f'{relative}: required source file is missing or unsafe')
        raw = path.read_bytes()
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        return raw.decode('utf-8')

    def document(relative, text, *, is_json=False):
        try:
            value = json.loads(text) if is_json else yaml.load(text, Loader=StrictLoader)
        except (ValueError, yaml.YAMLError, DuplicateKeyError) as exc:
            raise WorksHistoryError(f'{relative}: invalid document: {exc}') from exc
        return _mapping(value, relative)

    catalogs = []
    for kind, prefix in (('works', 'W'), ('plans', 'P')):
        index_path = f'{kind}/index.yaml'
        path = root / index_path
        if not path.exists() and not path.is_symlink():
            if (root / kind).exists() or (root / kind).is_symlink():
                raise WorksHistoryError(f'{index_path}: required source file is missing or unsafe')
            continue
        index = document(index_path, read(index_path))
        if index.get('version') != 1 or not isinstance(index.get('records'), list):
            raise WorksHistoryError(f'{index_path}: expected version 1 and records list')
        seen = set()
        for row in index['records']:
            _mapping(row, index_path)
            identifier = row.get('id')
            label = 'work' if kind == 'works' else 'plan'
            if not isinstance(identifier, str) or not re.fullmatch(prefix + r'[0-9]{4}', identifier):
                raise WorksHistoryError(f'{index_path}: invalid {label} id {identifier!r}')
            if identifier in seen:
                raise WorksHistoryError(f'{index_path}: duplicate {label} id {identifier}')
            seen.add(identifier)
            catalogs.append((kind, index_path, row))
    if not catalogs and not hashes:
        raise WorksHistoryError('requires works/index.yaml or plans/index.yaml')
    projects = []
    for kind, index_path, row in sorted(catalogs, key=lambda item: item[2]['id']):
        label = 'work' if kind == 'works' else 'plan'
        identifier = row['id']
        relative = row.get('path')
        if not isinstance(relative, str) or not re.fullmatch(kind + '/' + identifier + r'-[A-Za-z0-9_-]+', relative):
            raise WorksHistoryError(f'{index_path}: {identifier} has unsafe {label} path {relative!r}')
        directory = root / relative
        _real(directory)
        identities = []
        plan_metadata = {}
        if 'creator_id' in row:
            identities.append((index_path, row['creator_id']))
        # Public catalog lineage is the canonical creator identity when metadata
        # contains only catalog display fields. Both declarations must agree.
        for name in ('metadata.yaml', 'lineage.json'):
            path = directory / name
            if path.exists() or path.is_symlink():
                text = read(f'{relative}/{name}')
                metadata = document(f'{relative}/{name}', text, is_json=name.endswith('.json'))
                if name == 'metadata.yaml':
                    plan_metadata = metadata
                for key in ('id', 'record_id'):
                    if key in metadata and metadata[key] != identifier:
                        raise WorksHistoryError(f'{relative}/{name}: {label} identity differs from index')
                if 'creator_id' in metadata:
                    identities.append((f'{relative}/{name}', metadata['creator_id']))
        for location, creator in identities:
            if not isinstance(creator, str) or not creator.strip() or creator == 'unknown':
                raise WorksHistoryError(f'{location}: {identifier} requires explicit creator_id')
        if (not identities and creator_id is None) or len({creator for _, creator in identities}) > 1:
            raise WorksHistoryError(f'{relative}: missing or conflicting creator_id')
        source_creator = identities[0][1] if identities else None
        creator = creator_id if creator_id is not None else source_creator
        texts = []
        names = ('README.md', 'record.md') if kind == 'works' else ('plan.md',)
        for name in names:
            path = directory / name
            if path.exists() or path.is_symlink():
                texts.append((name, read(f'{relative}/{name}')))
        if kind == 'works' and not texts:
            raise WorksHistoryError(f'{relative}: requires README.md or record.md')
        # Claims are verbatim paragraph excerpts, not newly authored assertions.
        claims = []
        for name, text in texts:
            for ordinal, paragraph in enumerate(re.split(r'\n\s*\n', text), 1):
                if not paragraph.strip() or all(
                    line.lstrip().startswith('#') or not line.strip()
                    for line in paragraph.splitlines()
                ):
                    continue
                claims.append({'id': f'WH{len(claims) + 1:04}',
                               'statement': paragraph.strip(),
                               'source_ref': f'{relative}/{name}#paragraph-{ordinal}'})
        if kind == 'plans':
            # Use only explicitly textual comparison fields; IDs, hashes, assets,
            # lineage and index display text are not proposal content.
            def metadata_claims(value, keys=()):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in TEXT_FIELDS and isinstance(child, str) and child.strip():
                            claims.append({'id': f'WH{len(claims) + 1:04}',
                                           'statement': child,
                                           'source_ref': f'{relative}/metadata.yaml#' + '.'.join((*keys, key))})
                        elif isinstance(key, str) and key not in {'assets', 'self_repetition_risk'}:
                            metadata_claims(child, (*keys, key))
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        metadata_claims(child, (*keys, str(index)))
            metadata_claims(plan_metadata)
        if not claims:
            raise WorksHistoryError(f'{relative}: no comparable {label} text')
        target = identifier
        files[f'{target}/manifest.yaml'] = yaml.safe_dump({'project': {
            'id': f'works-history/{identifier}', 'creator_id': creator}}, allow_unicode=True, sort_keys=True)
        files[f'{target}/03_knowledge/claims.jsonl'] = ''.join(
            json.dumps(claim, ensure_ascii=False, sort_keys=True) + '\n' for claim in claims)
        # Preserve complete Markdown bytes (including headings) from the record,
        # falling back to the introduction for catalogs with no record.md yet.
        direction = dict(texts).get('record.md', dict(texts).get('README.md')) if kind == 'works' else dict(texts).get('plan.md', '')
        files[f'{target}/05_production/creative-direction.md'] = direction
        projects.append({f'{label}_id': identifier, 'project_id': f'works-history/{identifier}',
                         'creator_id': creator, 'source_path': relative, 'claim_count': len(claims),
                         **({'source_creator_id': source_creator} if creator_id is not None else {})})
    receipt = {'contract_version': 'works-history/v1', 'project_count': len(projects),
               'projects': projects, 'source_hashes': hashes}
    files['works-history.json'] = _json(receipt)
    if output.exists():
        if not output.is_dir():
            raise WorksHistoryError(f'{output}: output is not a directory')
        actual = {}
        for path in output.rglob('*'):
            _real(path)
            if path.is_file():
                actual[path.relative_to(output).as_posix()] = path.read_bytes().decode('utf-8')
        if actual == files:
            return receipt
        if any(output.iterdir()):
            raise WorksHistoryError(f'{output}: existing output differs; use a new history directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.works-history-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'history'
        staging.mkdir()
        for relative, text in files.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        staging.rename(output)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--works-root', required=True, type=Path, help='read-only catalog root containing works/index.yaml and/or plans/index.yaml')
    parser.add_argument('--creator-id', help='explicit authorized owner scope for the whole catalog; original identities remain in receipt')
    parser.add_argument('--output', required=True, type=Path, help='immutable external history directory')
    args = parser.parse_args()
    try:
        receipt = build_history(args.works_root, args.output, creator_id=args.creator_id)
    except (WorksHistoryError, InputParseError, OSError, ValueError) as exc:
        print(f'WORKS-HISTORY-INVALID: {exc}', file=sys.stderr)
        return 1
    print(_json(receipt), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
