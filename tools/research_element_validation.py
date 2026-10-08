"""Offline contract and grounding checks for the standalone element checkpoint."""
from functools import lru_cache
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from _common import load_json, read_jsonl


@lru_cache(maxsize=32)
def schema_validator(root, name):
    # Load only this schema's local reference closure. A broken unrelated
    # domain schema must remain a native SCHEMA-META finding, not crash the
    # optional element validator before that finding can be returned.
    resources = {}
    def collect(filename):
        if filename in resources:
            return
        path = root / 'schemas' / filename
        schema = load_json(path)
        if schema.get('$schema') != Draft202012Validator.META_SCHEMA['$id'] or not isinstance(schema.get('$id'), str):
            raise ValueError(f'{filename}: invalid or missing schema metadata')
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError(f'{filename}: invalid schema ({exc.validator})') from exc
        resources[filename] = schema
        def references(value):
            if isinstance(value, dict):
                ref = value.get('$ref', '').split('#', 1)[0]
                if ref:
                    if '/' in ref or not ref.endswith('.schema.json'):
                        raise ValueError(f'{path.name}: unsupported nonlocal schema reference')
                    collect(ref)
                for child in value.values():
                    references(child)
            elif isinstance(value, list):
                for child in value:
                    references(child)
        references(schema)
    collect(f'{name}.schema.json')
    registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in resources.values())
    schema = resources[f'{name}.schema.json']
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())


def require_schema(root, name, value):
    errors = list(schema_validator(root, name).iter_errors(value))
    if errors:
        error = errors[0]
        field = '.'.join(map(str, error.absolute_path)) or '$'
        raise ValueError(f'{name}#{field}: {error.validator} failed')


def check_project(project, protocol):
    """Return named grounding failures; do not repair or change project state."""
    path = project / '07_runtime/research-elements.json'
    if not path.exists():
        return []
    errors = []
    def fail(rule, message):
        errors.append((rule, message))
    try:
        state = load_json(path)
        require_schema(protocol, 'research-elements', state)
        from research_elements import canonical, digest
        from research_element_contracts import check_answer, require_message
        for relative, content in {**state['inputs'], **state['files']}.items():
            target = project / relative
            if target.resolve() != target or project not in target.parents or not target.is_file():
                fail('ELEMENT-PATH', f'Unsafe or missing element-owned file: {relative}')
            elif target.read_text(encoding='utf-8') != content:
                fail('ELEMENT-PROJECTION', f'File differs from its checkpoint: {relative}; run next to repair interrupted assembly.')
        for url, source in state['ledger'].items():
            if url != source['url'] or digest(source['body']) != source['content_hash']:
                fail('ELEMENT-SOURCE-HASH', 'Pinned source body/URL/hash mismatch')
        for identifier, value in state['answers'].items():
            accepted = [h for h in state['history'] if h['element_id'] == identifier and not h['failures']]
            if len(accepted) != 1:
                fail('ELEMENT-ANSWER-HISTORY', f'{identifier}: expected exactly one accepted answer')
                continue
            kind = 'search' if identifier.startswith('search.') else 'element'
            answer = {'contract_version': kind+'-answer/v1', 'run_id': state['run_id'], 'element_id': identifier,
                      'attempt': accepted[0]['attempt'], 'results' if kind == 'search' else 'value': value}
            require_message(answer, kind+'-answer')
            if digest(canonical(answer)) != accepted[0]['answer_hash']:
                fail('ELEMENT-ANSWER-HASH', f'{identifier}: accepted value differs from its recorded hash')
        excerpts = read_jsonl(project / state['config']['paths']['excerpts'])
        for excerpt in excerpts:
            require_schema(protocol, 'research-excerpt', excerpt)
            source = state['ledger'].get(excerpt['source_url'])
            if (not source or source['content_hash'] != excerpt['content_hash']
                    or source['body'][excerpt['start']:excerpt['end']] != excerpt['quote']):
                fail('ELEMENT-EXACT-EXCERPT', f"{excerpt['id']}: quote differs from the pinned body/offsets")
        for source in read_jsonl(project / state['config']['paths']['sources']):
            require_schema(protocol, 'retrieved-source', source)
            pinned = state['ledger'].get(source['url'])
            if not pinned or any(source[key] != pinned[key] for key in ('title', 'body', 'content_hash', 'acquired_at')):
                fail('ELEMENT-SOURCE-LEDGER', f"{source['id']}: source differs from the pinned retrieval")
        for evidence in read_jsonl(project / state['config']['paths']['evidence']):
            source = state['ledger'].get(evidence['source_location'])
            if not source or source['content_hash'] != evidence['content_hash']:
                fail('ELEMENT-EVIDENCE-SOURCE', f"{evidence['id']}: evidence is not grounded in the ledger")
        for prior in read_jsonl(project / state['config']['paths']['prior_art']):
            if prior['source_url'] not in state['ledger']:
                fail('ELEMENT-PRIOR-URL', f"{prior['id']}: prior-work URL is absent from the retrieved ledger")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        fail('ELEMENT-CHECKPOINT', f'{path.name}: {exc}')
    return errors
