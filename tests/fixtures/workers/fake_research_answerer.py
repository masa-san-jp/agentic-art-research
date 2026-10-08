"""Synthetic single-request answerer. It never fetches a real source."""
import json
import sys


def answer(request):
    envelope = {key: request[key] for key in ('run_id', 'element_id', 'attempt')}
    identifier = request['element_id']
    if request['contract_version'] == 'search-request/v1':
        return {**envelope, 'contract_version': 'search-answer/v1', 'results': [
            {'url': 'https://example.invalid/choice', 'title': 'Synthetic choice work',
             'body': 'Deferred choice keeps the shared image open to revision.'},
            {'url': 'https://example.invalid/finality', 'title': 'Synthetic final work',
             'body': 'A fixed image closes collective revision and demands a final choice.'}]}
    if identifier.startswith('question.'):
        value = 'How does deferred choice keep a shared image revisable?'
    elif identifier.startswith('query.'):
        value = 'deferred choice shared image ' + request['inputs']['strategy']
    elif identifier.startswith('relevance.'):
        value = {'answer': True, 'reason': request['inputs']['body']}
    elif identifier.startswith('excerpt.'):
        value = request['inputs']['body']
    elif identifier.startswith('observation.'):
        value = request['inputs']['quote']
    elif identifier.startswith('claim.'):
        value = request['inputs']['observation']
    elif identifier.startswith('claim-type.'):
        value = 'SOURCE_CLAIM'
    elif identifier.startswith('pair.'):
        value = 'opposes'
    else:
        raise ValueError('Unknown fake element ' + identifier)
    return {**envelope, 'contract_version': 'element-answer/v1', 'value': value}


if __name__ == '__main__':
    print(json.dumps(answer(json.load(sys.stdin)), ensure_ascii=False))
