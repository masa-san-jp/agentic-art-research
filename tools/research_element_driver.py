"""Deterministic Stage B scheduling and file assembly. No model or network calls."""
from __future__ import annotations
import json
import hashlib
from math import ceil
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
        records['questions'].append({'id': qid, 'question': answers[identifier], 'priority': 'mandatory', 'status': 'OPEN',
                                     'stop_condition': {'sufficient_answers': state['budget']['sufficient_answers'],
                                        'max_search_strategies': min(len(state['config']['search_strategies']), state['stopping']['max_search_strategies_per_question']),
                                        'max_sources_reviewed': state['stopping']['max_sources_reviewed_per_question']}})
    yield from collect(state, records)


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
    state['files'][state['config']['paths']['log']] = state['baseline_log'] + ''.join(
        json.dumps(event, ensure_ascii=False, sort_keys=True)+'\n' for event in records['log'])
    if not pending:
        state['completion'] = {'stage': 'B', 'status': 'INCOMPLETE', 'reason': 'Driver milestones still in progress; production gates are unchanged.'}


def chunks(body, limit):
    """Lossless UTF-8 chunks; no normalized text can pass the quotation check."""
    start, size = 0, 0
    for i, char in enumerate(body):
        width = len(char.encode('utf-8'))
        if size + width > limit:
            yield body[start:i], start
            start, size = i, 0
        size += width
    if start < len(body):
        yield body[start:], start


def occurred(state, identifier):
    return next(h['occurred_at'] for h in state['history'] if h['element_id'] == identifier and not h['failures'])


def event(state, records, kind, identifier, **fields):
    records['log'].append({'event_id': f"ELEMENT-{state['run_id']}-{kind}-{identifier}-" + hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:12],
                           'event_type': kind, 'occurred_at': occurred(state, identifier), **fields})


def collect(state, records):
    from research_elements import digest
    from stopping_policy import metrics_from_events
    answers, config = state['answers'], state['config']
    # Register every retrieved body, including irrelevant results and aliases.
    hash_owner = {}
    for index, (url, source) in enumerate(state['ledger'].items(), 1):
        rights = config['rights_by_url'].get(url, config['rights_default'])
        records['sources'].append({**source, 'id': f'SRC{index:03}',
                                  'duplicate_of': hash_owner.get(source['content_hash']), **rights})
        hash_owner.setdefault(source['content_hash'], url)
    evidence_by_hash = {}
    fetched = 0
    for q in records['questions']:
        qid = q['id']
        seen, saturation, found = set(), 0, set()
        reason = 'search_strategy_limit'
        for strategy in config['search_strategies'][:q['stop_condition']['max_search_strategies']]:
            if len(found) >= state['budget']['sufficient_answers']:
                reason = 'sufficient_answers'
                break
            if saturation >= state['stopping']['saturation_rounds_without_new_evidence']:
                reason = 'evidence_saturation'
                break
            remaining = min(state['budget']['max_total_sources'] - fetched,
                            q['stop_condition']['max_sources_reviewed'] - len(seen))
            if remaining <= 0:
                reason = 'total_source_limit' if fetched >= state['budget']['max_total_sources'] else 'source_review_limit'
                break
            prefix = f'{qid}.{strategy}'
            query_id = f'query.{prefix}'
            yield element(state, query_id, '問いを調べる検索語を1つ返してください。',
                          {'question': q['question'], 'strategy': strategy}, max_chars=160,
                          checks=['contains_source_terms:question'])
            search_id = f'search.{prefix}'
            request = {'contract_version': 'search-request/v1', 'run_id': state['run_id'],
                       'element_id': search_id, 'attempt': state['attempt'], 'previous_failure': state['previous_failure'],
                       'query': answers[query_id], 'limit': min(config['search_limit'], remaining)}
            require_message(request, 'search-request')
            # Exploration is logged before asking the external agent to search.
            event(state, records, 'SEARCH_ATTEMPT', query_id, question_id=qid, strategy_id=strategy)
            yield request
            results = answers[search_id]
            fetched += len(results)
            before = len(found)
            for result in results:
                source = state['ledger'][result['url']]
                body_hash = source['content_hash']
                if body_hash in seen:
                    continue
                seen.add(body_hash)
                source_id = next(r['id'] for r in records['sources'] if r['url'] == source['url'])
                event(state, records, 'SOURCE_REVIEWED', search_id, question_id=qid, source_id=source_id)
                # All windows are offered separately; no source is silently truncated.
                for part, (body, offset) in enumerate(chunks(source['body'], config['body_chunk_bytes']), 1):
                    prefix_source = f'{qid}.{source_id}.{part}'
                    relevance_id = f'relevance.{prefix_source}'
                    yield element(state, relevance_id, 'この取得本文は問いに関係しますか。はい/いいえと理由1文を返してください。',
                                  {'question': q['question'], 'title': source['title'], 'body': body}, boolean=True,
                                  checks=['one_sentence', 'contains_source_terms:body'])
                    if not answers[relevance_id]['answer']:
                        continue
                    excerpt_id = f'excerpt.{prefix_source}'
                    # The full body is pinned in the internal ledger; only one window is sent.
                    yield element(state, excerpt_id, '問いの根拠になる本文の一部分を、そのまま1つ抜き書きしてください。',
                                  {'question': q['question'], 'body': body, 'source_url': source['url']},
                                  max_chars=240, checks=['exact_excerpt:source_url'])
                    quote = answers[excerpt_id]
                    # Distinct windows of the same source are one independent source.
                    if body_hash not in evidence_by_hash:
                        eid = f"EV{len(records['evidence'])+1:03}"
                        rights = config['rights_by_url'].get(source['url'], config['rights_default'])
                        records['evidence'].append({'id': eid, 'source_type': 'agent-retrieved-public',
                            'source_location': source['url'], 'created_at': 'unknown', 'acquired_at': source['acquired_at'],
                            'content_hash': body_hash, **rights, 'related_projects': [state['project_id']],
                            'related_questions': [], 'extraction_status': 'processed', 'direct_observation': False,
                            'observed_by': 'research-elements'})
                        evidence_by_hash[body_hash] = records['evidence'][-1]
                    evidence = evidence_by_hash[body_hash]
                    if qid not in evidence['related_questions']:
                        evidence['related_questions'].append(qid)
                    # A quote must come from the supplied window too, not any hidden part.
                    start = offset + body.index(quote)
                    records['excerpts'].append({'id': f"EX{len(records['excerpts'])+1:03}", 'element_id': excerpt_id,
                        'evidence_id': evidence['id'], 'question_id': qid, 'source_url': source['url'],
                        'content_hash': body_hash, 'quote': quote, 'start': start, 'end': start + len(quote)})
                    if body_hash not in found:
                        found.add(body_hash)
                        event(state, records, 'ANSWER_FOUND', excerpt_id, question_id=qid, evidence_id=evidence['id'])
            new_count = len(found) - before
            saturation = saturation + 1 if new_count == 0 else 0
            event(state, records, 'EVIDENCE_ROUND', search_id, question_id=qid, new_evidence_count=new_count)
        if len(found) >= state['budget']['sufficient_answers']:
            reason = 'sufficient_answers'
        q.update(status='ANSWERED' if reason == 'sufficient_answers' else 'UNRESOLVED', terminal_reason=reason)
        if state['status'] == 'BLOCKED':
            q.update(status='UNRESOLVED', terminal_reason=state['blocked']['last_failure'][0]['check'])
    records['rights'] = [{'evidence_id': row['id'], 'rights_status': row['rights_status'],
                          'redistribution': row['redistribution'],
                          'material_adoption': 'REJECTED' if row['rights_status'] == 'unknown' else 'REVIEW_REQUIRED',
                          'reason': 'Retrieved text is research evidence; no permission to adopt or redistribute material is inferred.'}
                         for row in records['evidence']]
