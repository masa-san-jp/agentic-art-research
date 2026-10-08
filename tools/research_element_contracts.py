"""Pinned parent wire contracts; only validated single-value envelopes cross this boundary."""
from functools import lru_cache
from jsonschema import Draft202012Validator
from _common import ROOT, load_json

CONTRACTS = ('element-request', 'element-answer', 'search-request', 'search-answer')

@lru_cache(maxsize=4)
def validator(name):
    if name not in CONTRACTS:
        raise ValueError('Unsupported element contract')
    schema = load_json(ROOT / 'schemas' / f'{name}.schema.json')
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def require_message(value, name):
    errors = list(validator(name).iter_errors(value))
    if errors:
        error = errors[0]
        field = '.'.join(map(str, error.absolute_path)) or '$'
        raise ValueError(f'{name}#{field}: {error.validator} failed')


def check_answer(request, answer, ledger, previous):
    from research_element_checks import check_value
    value = answer['value']
    fmt = request['answer_format']
    text = value.get('reason', '') if isinstance(value, dict) else value
    failures = []
    if (fmt['type'] == 'boolean_with_reason') != isinstance(value, dict):
        return [{'check': 'answer_format', 'reason': 'Use the requested value type.'}]
    if not fmt.get('min_chars', 1) <= len(text) <= fmt.get('max_chars', 2048):
        failures.append({'check': 'character_count', 'reason': 'Respect the requested character limits.'})
    if fmt['type'] == 'choice' and value not in fmt['choices']:
        failures.append({'check': 'choice', 'reason': 'Return exactly one supplied choice.'})
    return failures + check_value(value, request['checks'], inputs=request['inputs'],
                                  ledger=ledger, previous_answers=previous)
