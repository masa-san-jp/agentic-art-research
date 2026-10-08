"""Deterministic Stage B scheduling and file assembly. No model or network calls."""
from __future__ import annotations
import json
import hashlib
from itertools import combinations
import yaml
from research_element_contracts import require_message


def managed_empty_files(config):
    keys = {'questions': 'questions', 'insights': 'insights', 'decisions': 'decisions',
            'rejected': 'rejected_options', 'rights': 'rights', 'uncertainties': 'uncertainties', 'reviews': 'reviews'}
    return {path: {keys[key]: []} if key in keys else None
            for key, path in config['paths'].items() if key not in ('state', 'lock', 'log', 'repetition_report')}


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
                      {'proposition': state['proposition'],
                       'question_number': index+1,
                       'previous_questions': [q['question'] for q in records['questions']]},
                      checks=['ends_with_question', 'one_sentence', 'not_similar:0.8'], max_chars=120)
        records['questions'].append({'id': qid, 'question': answers[identifier], 'priority': 'mandatory', 'status': 'OPEN',
                                     'stop_condition': {'sufficient_answers': state['budget']['sufficient_answers'],
                                        'max_search_strategies': min(len(state['config']['search_strategies']), state['stopping']['max_search_strategies_per_question']),
                                        'max_sources_reviewed': state['stopping']['max_sources_reviewed_per_question']}})
    yield from collect(state, records)
    yield from analyze(state, records)
    yield from decide(state, records)


def advance(state):
    records = {key: [] for key in state['config']['paths'] if key not in ('state', 'lock', 'repetition_report')}
    pending = None
    for request in schedule(state, records):
        if request['element_id'] not in state['answers']:
            pending = request
            break
    if state['status'] == 'BLOCKED':
        for q in records['questions']:
            if q['status'] == 'OPEN':
                q.update(status='UNRESOLVED', terminal_reason=state['blocked']['last_failure'][0]['check'])
    if state['status'] != 'BLOCKED':
        state.update(pending=pending, status='WAITING' if pending else 'COMPLETED')
    keys = {'questions': 'questions', 'insights': 'insights', 'decisions': 'decisions',
            'rejected': 'rejected_options', 'rights': 'rights', 'uncertainties': 'uncertainties', 'reviews': 'reviews'}
    state['record_counts'] = {key: len(rows) for key, rows in records.items()}
    state['question_gaps'] = [q['id'] for q in records['questions'] if q['status'] == 'UNRESOLVED']
    state['files'] = {state['config']['paths'][key]: yaml.safe_dump({keys[key]: rows}, allow_unicode=True, sort_keys=False)
                      if key in keys else ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in rows)
                      for key, rows in records.items() if key != 'log'}
    state['files'][state['config']['paths']['log']] = state['baseline_log'] + ''.join(
        json.dumps(event, ensure_ascii=False, sort_keys=True)+'\n' for event in records['log'])



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
    answers, config = state['answers'], state['config']
    # Register every retrieved body, including irrelevant results and aliases.
    hash_owner = {}
    for source in sorted(state['ledger'].values(), key=lambda row: row['id']):
        url = source['url']
        rights = config['rights_by_url'].get(url, config['rights_default'])
        records['sources'].append({**source,
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
                metadata_bytes = max(len(json.dumps(inputs, ensure_ascii=False).encode()) for inputs in (
                    {'question': q['question'], 'title': source['title'], 'body': ''},
                    {'question': q['question'], 'body': '', 'source_url': source['url']}))
                window_bytes = min(config['body_chunk_bytes'], config['input_bytes'] - metadata_bytes)
                for part, (body, offset) in enumerate(chunks(source['body'], window_bytes), 1):
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


def analyze(state, records):
    answers = state['answers']
    for excerpt in records['excerpts']:
        oid = f"OB{len(records['observations'])+1:03}"
        identifier = f'observation.{oid}'
        question = next(q['question'] for q in records['questions'] if q['id'] == excerpt['question_id'])
        yield element(state, identifier, 'この抜き書きから観察できることを1文で返してください。',
                      {'question': question, 'quote': excerpt['quote']},
                      checks=['one_sentence', 'contains_source_terms:quote'])
        records['observations'].append({'id': oid, 'statement': answers[identifier],
            'scope': excerpt['question_id'], 'evidence_ids': [excerpt['evidence_id']]})
    for observation in records['observations']:
        cid = f"CL{len(records['claims'])+1:03}"
        identifier = f'claim.{cid}'
        yield element(state, identifier, 'この観察が根拠となる主張を1文で返してください。',
                      {'observation': observation['statement']},
                      checks=['one_sentence', 'contains_source_terms:observation'])
        type_id = f'claim-type.{cid}'
        yield element(state, type_id, 'この主張の種類を1つ選んでください。',
                      {'claim': answers[identifier], 'observation': observation['statement']},
                      choices=state['vocabulary']['claim_types'])
        kind = answers[type_id]
        # Retrieved text is not independent verification. Preserve epistemic caution.
        status = 'HYPOTHESIS' if kind == 'HYPOTHESIS' else 'WEAK'
        records['claims'].append({'id': cid, 'statement': answers[identifier], 'type': kind,
            'evidence_ids': observation['evidence_ids'], 'supporting_claims': [], 'opposing_claims': [],
            'scope': observation['scope'], 'epistemic_status': status})
    # Earlier-to-later order makes the support graph acyclic; opposition is symmetric.
    for left, right in combinations(records['claims'], 2):
        identifier = f"pair.{left['id']}.{right['id']}"
        yield element(state, identifier, '左の主張は右の主張を支持、対立、無関係のどれにしますか。1つ選んでください。',
                      {'left': left['statement'], 'right': right['statement']},
                      choices=state['config']['pair_relations'])
        relation = answers[identifier]
        if relation == 'unrelated':
            continue
        records['relationships'].append({'id': f"RL{len(records['relationships'])+1:03}",
            'from_id': left['id'], 'to_id': right['id'],
            'type': 'refers_to' if relation == 'supports' else 'contrasts_with',
            'rationale': f"{left['id']} {relation} {right['id']} (element {identifier}).",
            'evidence_ids': list(dict.fromkeys(left['evidence_ids'] + right['evidence_ids']))})
        if relation == 'supports':
            right['supporting_claims'].append(left['id'])
        else:
            left['opposing_claims'].append(right['id'])
            right['opposing_claims'].append(left['id'])
            left['epistemic_status'] = right['epistemic_status'] = 'CONTESTED'
            records['contradictions'].append({'id': f"CT{len(records['contradictions'])+1:03}",
                'claim_ids': [left['id'], right['id']], 'description': f"{left['statement']} / {right['statement']}",
                'status': 'OPEN', 'resolution': None})


def decide(state, records):
    answers = state['answers']
    group_size = state['config']['insight_group_size']
    for q in records['questions']:
        claims = [c for c in records['claims'] if c['scope'] == q['id']]
        for start in range(0, len(claims), group_size):
            group = claims[start:start+group_size]
            iid = f"IN{len(records['insights'])+1:03}"
            identifier = f'insight.{iid}'
            yield element(state, identifier, 'この主張から得られる洞察を1文で返してください。対立がある場合は断定を避けてください。',
                          {'question': q['question'], 'claims': [{k: c[k] for k in ('id', 'statement', 'epistemic_status')} for c in group]},
                          checks=['one_sentence'])
            opposing = list(dict.fromkeys(cid for c in group for cid in c['opposing_claims']))
            records['insights'].append({'id': iid, 'statement': answers[identifier],
                'claim_ids': [c['id'] for c in group], 'opposing_claim_ids': opposing,
                'epistemic_status': 'CONTESTED' if opposing else 'WEAK',
                # This is the research implication itself. Stage C authors the work content.
                'production_implication': answers[identifier]})
    for insight in records['insights']:
        did = f"DC{len(records['decisions'])+1:03}"
        question_id = f'decision-question.{did}'
        yield element(state, question_id, 'この洞察を作品に生かすために決める問いを1文で返してください。',
                      {'insight': insight['statement']}, checks=['ends_with_question', 'one_sentence'], max_chars=120)
        options = {}
        for index in range(state['config']['options_per_decision']):
            option_id = f'{did}-option-{index+1}'
            identifier = f'option.{option_id}'
            yield element(state, identifier, 'この判断の問いへの選択肢を1つ、既出と異なる内容で返してください。',
                          {'question': answers[question_id], 'insight': insight['statement'], 'previous_options': list(options.values())},
                          checks=['one_sentence', 'not_similar_to:previous_options'], max_chars=160)
            options[option_id] = answers[identifier]
        adoption_id = f'adopt.{did}'
        yield element(state, adoption_id, '洞察に基づいて採用する選択肢のIDを1つ選んでください。',
                      {'question': answers[question_id], 'insight': insight['statement'], 'options': options}, choices=list(options))
        selected = answers[adoption_id]
        rejected = []
        for option_id, option in options.items():
            if option_id == selected:
                continue
            identifier = f'reject.{option_id}'
            yield element(state, identifier, 'この選択肢を採用しない理由を1文で返してください。',
                          {'insight': insight['statement'], 'selected': options[selected], 'rejected': option}, checks=['one_sentence'])
            rid = f"RO{len(records['rejected'])+1:03}"
            records['rejected'].append({'id': rid, 'title': option, 'reason': answers[identifier], 'decision_ids': [did]})
            rejected.append(rid)
        uncertainty_id = f"U{len(records['uncertainties'])+1:03}"
        uncertainty = ('Research-grounded selection; production and viewer validation have not been performed.'
                       + (' Opposing claims remain unresolved.' if insight['opposing_claim_ids'] else ''))
        records['uncertainties'].append({'id': uncertainty_id, 'statement': uncertainty, 'status': 'OPEN',
            'severity': 'MAJOR' if insight['opposing_claim_ids'] else 'MINOR', 'decision_ids': [did],
            'review_trigger': 'Stage C production design and prototype validation.'})
        evidence_ids = list(dict.fromkeys(eid for c in records['claims'] if c['id'] in insight['claim_ids'] for eid in c['evidence_ids']))
        records['decisions'].append({'id': did, 'question': answers[question_id], 'selected_option': options[selected],
            'rejected_options': [o for key, o in options.items() if key != selected], 'rejected_option_ids': rejected,
            'insight_ids': [insight['id']], 'evidence_ids': evidence_ids,
            'reason': f"{selected} selected under {insight['id']}: {insight['statement']}",
            'uncertainty': uncertainty, 'uncertainty_ids': [uncertainty_id],
            'review_trigger': 'Stage C production design and prototype validation.', 'authority': 'agent-recommended', 'status': 'ADOPTED'})
    # Inspect each evidence-backed source; a generic article is never invented into a work title.
    for evidence in records['evidence']:
        source = state['ledger'][evidence['source_location']]
        quotes = [e['quote'] for e in records['excerpts'] if e['evidence_id'] == evidence['id']]
        identifier = f"prior-work.{evidence['id']}"
        yield element(state, identifier, 'この取得資料には比較する先行作品が記述されていますか。はい/いいえと理由1文を返してください。',
                      {'title': source['title'], 'quotes': quotes[:state['config']['insight_group_size']]}, boolean=True, checks=['one_sentence'])
        if not answers[identifier]['answer']:
            continue
        # Title and URL are pinned retrieval metadata; no URL generation request.
        for decision in records['decisions']:
            if evidence['id'] not in decision['evidence_ids']:
                continue
            identifier = f"difference.{evidence['id']}.{decision['id']}"
            yield element(state, identifier, 'この資料の先行作品と採用案の違いを1文で返してください。資料にない事実は加えないでください。',
                          {'title': source['title'], 'quote': quotes[0],
                           'selected_option': decision['selected_option']}, checks=['one_sentence', 'contains_source_terms:quote,selected_option'])
            records['prior_art'].append({'id': f"PA{len(records['prior_art'])+1:03}",
                'work_title': source['title'], 'source_url': source['url'], 'difference': answers[identifier],
                'relation_to_proposal': f"Evidence {evidence['id']} for decision {decision['id']}.",
                'rights_status': evidence['rights_status'], 'access_class': evidence['sensitivity']})
