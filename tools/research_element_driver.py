"""Deterministic Stage B scheduling and file assembly. No model or network calls."""
from __future__ import annotations
import json
import yaml
from _common import stable_json
from research_element_contracts import require_message


def managed_empty_files(config):
    keys = {'questions': 'questions', 'insights': 'insights', 'decisions': 'decisions',
            'rejected': 'rejected_options', 'rights': 'rights'}
    return {path: {keys[key]: []} if key in keys else None
            for key, path in config['paths'].items() if key not in ('state', 'lock', 'log')}


def element(state, identifier, instruction, inputs, *, choices=None, boolean=False, checks=(), max_chars=240):
    fmt = {'type': 'choice', 'choices': choices} if choices else {
        'type': 'boolean_with_reason' if boolean else 'text', 'max_chars': max_chars}
    request = {'contract_version': 'element-request/v1', 'run_id': state['run_id'],
               'element_id': identifier, 'attempt': state['attempt'], 'previous_failure': state['previous_failure'],
               'instruction': instruction, 'inputs': inputs, 'answer_format': fmt,
               'checks': ['non_empty', 'forbidden:' + ','.join(state['config']['forbidden']), *checks]}
    if len(json.dumps(inputs, ensure_ascii=False).encode()) > state['config']['input_bytes']:
        raise ValueError(f'{identifier}: inputs exceed the configured request byte budget')
    require_message(request, 'element-request')
    return request


def schedule(state, records):
    answers = state['answers']
    for index in range(state['budget']['question_count']):
        qid = f'Q{index+1:03}'
        identifier = f'question.{qid}'
        yield element(state, identifier, '命題について調べる問いを1文で返してください。',
                      {'proposition': state['inputs']['00_intake/creative-intent.md'],
                       'question_number': index+1,
                       'previous_questions': [q['question'] for q in records['questions']]},
                      checks=['ends_with_question', 'one_sentence', 'not_similar:0.8'])
        records['questions'].append({'id': qid, 'question': answers[identifier], 'priority': 'mandatory', 'status': 'OPEN'})


def advance(state):
    records = {key: [] for key in state['config']['paths'] if key not in ('state', 'lock')}
    pending = None
    for request in schedule(state, records):
        if request['element_id'] not in state['answers']:
            pending = request
            break
    if state['status'] != 'BLOCKED':
        state.update(pending=pending, status='WAITING' if pending else 'COMPLETED')
    keys = {'questions': 'questions', 'insights': 'insights', 'decisions': 'decisions',
            'rejected': 'rejected_options', 'rights': 'rights'}
    state['files'] = {state['config']['paths'][key]: yaml.safe_dump({keys[key]: rows}, allow_unicode=True, sort_keys=False)
                      if key in keys else ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in rows)
                      for key, rows in records.items() if key != 'log'}
    state['files'][state['config']['paths']['log']] = state['baseline_log']
    if not pending:
        state['completion'] = {'stage': 'B', 'status': 'INCOMPLETE', 'reason': 'Driver milestones still in progress; production gates are unchanged.'}
