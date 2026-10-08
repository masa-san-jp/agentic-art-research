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
        questions = [
            'How can collective editing keep a deferred choice available?',
            'Which display rules affect when participants revise the image?',
            'What visual traces distinguish provisional decisions from fixed outcomes?',
            'How do different audiences negotiate finality in a shared composition?',
            'Where does authorship reside when viewers alter an unfinished image?',
            'When should a collective composition stop changing during an exhibition?',
            'What permissions shape an open revision process for contributors?',
            'How can an archive preserve discarded image choices for later study?',
            'Which material constraints resist repeated changes to a composition?',
            'What social pressures make a tentative choice feel final?',
            'How does delayed feedback influence decisions about a shared image?',
            'What documentation helps participants understand earlier revisions?']
        value = questions[int(identifier.split('Q')[-1]) - 1]
    elif identifier.startswith('query.'):
        value = request['inputs']['question'].rstrip('?') + ' ' + request['inputs']['strategy']
    elif identifier.startswith('relevance.'):
        value = {'answer': True, 'reason': request['inputs']['body']}
    elif identifier.startswith('excerpt.'):
        value = request['inputs']['body']
    elif identifier.startswith('observation.'):
        value = ('Collective authorship appears constrained when participants cannot alter the composition.'
                 if 'fixed' in request['inputs']['quote'].lower() else
                 'Participants retain editing opportunities because the shared image remains provisional.')
    elif identifier.startswith('claim.'):
        value = ('Fixing a collective composition may reduce the scope of later participant authorship.'
                 if 'constrained' in request['inputs']['observation'] else
                 'A revisable shared image can preserve participant agency beyond an initial selection.')
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
