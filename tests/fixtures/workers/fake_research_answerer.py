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
    elif identifier.startswith('insight.'):
        value = 'Deferred choice and a fixed image suggest a tension over collective revision.'
    elif identifier.startswith('decision-question.'):
        value = 'Should collective revision remain possible in the shared image?'
    elif identifier.startswith('option.'):
        value = 'Keep the shared image open to collective revision.' if identifier.endswith('-1') else 'Lock the final composition once the exhibition opens.'
    elif identifier.startswith('adopt.'):
        value = request['answer_format']['choices'][0]
    elif identifier.startswith('reject.'):
        value = 'Locking the final composition prevents the deferred choice from remaining available.'
    elif identifier.startswith('prior-work.'):
        value = {'answer': True, 'reason': 'The synthetic title describes a comparison work.'}
    elif identifier.startswith('difference.'):
        value = 'The fixed image closes revision while the selected shared image keeps deferred choice open.'
    else:
        raise ValueError('Unknown fake element ' + identifier)
    return {**envelope, 'contract_version': 'element-answer/v1', 'value': value}


if __name__ == '__main__':
    print(json.dumps(answer(json.load(sys.stdin)), ensure_ascii=False))
