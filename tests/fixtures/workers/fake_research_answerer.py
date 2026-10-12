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
    elif identifier.startswith(('plan.', 'point.', 'prototype.', 'requirement.', 'acceptance.')):
        value = stage_c(request, identifier)
    else:
        raise ValueError('Unknown fake element ' + identifier)
    return {**envelope, 'contract_version': 'element-answer/v1', 'value': value}


STAGE_C = {
    'title': ['Open Revision Wall', 'Locked Frame Archive'],
    'proposition': ['A shared image stays open when each visitor may revise one mark and the earlier marks remain visible.',
                    'An image locked at opening shows what a collective loses when a final choice closes revision.'],
    'core-question': ['Who decides when a shared image is finished?', 'What does a final choice hide from later visitors?'],
    'experience': 'Visitors see the wall change mark by mark and notice that their own revision will be revised.',
    'component': ['A large paper wall with a printed grid', 'A stamp kit with one ink color per day',
                  'A logbook where each change is dated'],
    'viewer-action': 'Each visitor redraws one square of the grid and signs the logbook.',
    'material': 'Water-based ink on paper that fades under the gallery light over the exhibition weeks.',
    'leap': 'The work turns revision itself into the subject by keeping every overwritten mark legible.',
    'exclude': 'Digital screens',
    'difference': 'Unlike the fixed final work in the source, the selected wall keeps deferred choice open to every visitor.',
    'step': ['Print the grid on a roll of paper and mount it on the wall.',
             'Prepare the stamp kit and the dated logbook beside the wall.',
             'Run a one-day trial with ten visitors and record every change.'],
    'step-done': 'The grid, kit and logbook are in place and photographed from the visitor position.',
    'step-effect': 'PHYSICAL_EXTERNAL',
    'technical': 'Paper, stamps and a logbook need no custom technology and can be built in a week.',
    'cost': 'LOW', 'duration': 'WEEKS', 'venue': 'MEDIUM',
    'uncertainty': 'Whether visitors dare to overwrite marks made by strangers is unknown.',
}
POINTS = {
    'encounter': ['A visitor enters the room and sees a half-redrawn grid with a queue at the stamp table.',
                  'A returning visitor finds the square she drew yesterday already redrawn by someone else.',
                  'A school group reads the logbook aloud and argues about which version was best.'],
    'position': 'The visitor stands one meter from the wall with the logbook at hand height.',
    'first-seconds': 'The visitor notices overlapping ink layers of different colors on one grid.',
    'after-30s': 'The visitor finds the logbook and sees that every square has been redrawn several times.',
    'after-3min': 'The visitor leaves unsure whether her own mark will survive until the end of the exhibition.',
    'field': 'Participatory drawing installation',
    'stands-against': 'The museum habit of presenting a collective work only in its finished state.',
    'claim': 'A collective image is honest only while its revisions remain visible to everyone.',
    'who-disagrees': 'Collectors who need a single final object to buy',
    'denies': 'The idea that a shared work must end in one final composition.',
    'shown-not-told': 'How each visitor quietly overwrites a stranger without ever meeting them.',
    'mechanism': ['Visitors stamp over the grid and the ink fades slowly, so each layer stays faintly visible.',
                  'Each day uses a new ink color and the logbook dates it, so the order of every revision can be read from the wall itself.'],
}


def stage_c(request, identifier):
    """Synthetic Stage C values: the first mechanism is judged merely plainer, the second ceases to work."""
    parts = identifier.split('.')
    if parts[0] == 'plan':
        plan, item = int(parts[1][2:]) - 1, parts[2]
        value = STAGE_C[item]
        if item in ('title', 'proposition', 'core-question'):
            return value[plan]
        if item in ('component', 'step'):
            return value[int(parts[3]) - 1]
        return value
    if parts[0] == 'point':
        item = parts[2]
        if item == 'encounter':
            return POINTS['encounter'][int(parts[3]) - 1]
        if item == 'mechanism':
            return POINTS['mechanism'][min(int(parts[3]), 2) - 1]
        if item == 'without-technique':
            if parts[3] == '1':
                return {'answer': False, 'reason': 'Without fading ink the wall still works as an ordinary drawing board.'}
            return {'answer': True, 'reason': 'Without dated colors the order of revisions cannot be read at all.'}
        return POINTS[item]
    if parts[0] == 'prototype':
        return ('Will strangers overwrite each other within one trial day?' if parts[2] == 'question'
                else 'Invite ten visitors for one day and count overwritten squares in the logbook.')
    if parts[0] == 'requirement':
        return {'experience': 'Within three minutes a visitor must see at least two layers of revision on one square.',
                'material': 'The ink must remain legible after two weeks under gallery light.',
                'interaction': 'Every visitor must be able to redraw one square without staff help.',
                'mechanism': 'Each day of revision must be readable from its ink color and logbook date.'}[parts[2]]
    if parts[2] == 'method':
        return request['answer_format']['choices'][0]
    return 'Five of five observed squares show the expected result when checked against the logbook.'


if __name__ == '__main__':
    print(json.dumps(answer(json.load(sys.stdin)), ensure_ascii=False))
