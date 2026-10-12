"""Deterministic Stage C scheduling and file assembly. No model or network calls.

Stage C turns completed Stage B research into the work's content: work plans item by item,
the key points of the recommended plan, its prototype, requirements and acceptance tests.
Every value is one answer to one request; choosing decisions, comparing plans, transcribing
research and writing files are program work.
"""
from __future__ import annotations

import json
from copy import deepcopy

import yaml

from research_element_driver import element

PLAINER = 'MERELY_PLAINER'
CEASES = 'CEASES_TO_WORK'
# Whole answers that leave "who disagrees" empty in substance; the same set production rejects
# (agentic-art-production tools/lib/production_brief.py), compared as whole values.
NOBODY = 'なし,特にいない,特にいません,誰もいない,誰も反対しない,反対者はいない,none,no one,nobody,no-one'


def fit(state, inputs, optional=()):
    """Keep a request inside the byte budget: drop optional inputs, then shorten the longest strings.

    Inputs named by a check are never in `optional`, so a check never loses its material.
    """
    inputs = deepcopy(inputs)  # never shorten the caller's lists
    limit = state['config']['input_bytes']

    def size():
        return len(json.dumps(inputs, ensure_ascii=False).encode())
    for key in optional:
        if size() <= limit:
            break
        value = inputs.get(key)
        if isinstance(value, list):
            while value and size() > limit:
                value.pop()
        if size() > limit:
            inputs.pop(key, None)
    while size() > limit:
        strings = [k for k, v in inputs.items() if isinstance(v, str) and v]
        if not strings:
            raise ValueError('Stage C inputs exceed the configured request byte budget')
        key = max(strings, key=lambda k: len(inputs[k].encode()))
        raw = inputs[key].encode('utf-8')
        inputs[key] = raw[:max(0, len(raw) - max(3, size() - limit))].decode('utf-8', errors='ignore')
    return inputs


def ask(state, identifier, instruction, inputs, optional=(), **kwargs):
    """Yield one request; return the accepted value, or None when the element was abandoned."""
    kwargs.setdefault('max_chars', 240)
    checks = list(kwargs.pop('checks', ()))
    if not kwargs.get('choices') and not kwargs.get('boolean'):
        # A value accepted here is written into the work's files; "なし" or "TBD" must be retried now,
        # not discovered when the files are assembled.
        checks.append('not_placeholder')
    if 'ends_with_question' in checks:
        instruction += '最後は「？」で終えてください。'
    yield element(state, identifier, instruction, fit(state, inputs, optional), checks=checks, **kwargs)
    if identifier in state.get('abandoned', {}):
        return None
    return state['answers'][identifier]


def abandonable(state, identifier):
    """Optional items and every plan but the first are skipped instead of blocking the run."""
    parts = identifier.split('.')
    if identifier.startswith('plan.') and len(parts) >= 3:
        return parts[2] == 'exclude' or parts[1] != state['plans'][0]['id']
    # An unanswerable reference category stays unmapped; production reports it as a reference gap.
    return identifier.startswith('reference.')


def select_plans(research, count):
    """Distinct adopted Stage B decisions, best supported first (program, not inference)."""
    insights = {i['id']: i for i in research['insights']}

    def weight(decision):
        insight = insights.get(decision['insight_ids'][0], {})
        return (-len(decision['evidence_ids']), -len(insight.get('claim_ids', [])), decision['id'])
    plans, seen = [], set()
    for decision in sorted(research['decisions'], key=weight):
        key = ' '.join(decision['selected_option'].split())
        if key in seen:
            continue
        seen.add(key)
        plans.append({'id': f'PH{len(plans)+1:03}', 'decision_id': decision['id']})
        if len(plans) >= count:
            break
    return plans


def precedents_for(research, decision_id):
    """Stage B prior-art records written against this decision; others only when there are none.

    A difference written against another decision's option is labelled with that decision, so it is
    never presented as a difference of this plan.
    """
    own = [p for p in research['prior_art'] if p['relation_to_proposal'].endswith(f'decision {decision_id}.')]
    if own:
        return own
    other = []
    for p in research['prior_art']:
        decision = p['relation_to_proposal'].rsplit('decision ', 1)[-1].rstrip('.')
        other.append({**p, 'difference': f"（判断 {decision} の採用案との違いとして記録）{p['difference']}"})
    return other


# Optional work fields, dropped first when a request would exceed the byte budget.
WORK_OPTIONAL = ('claims', 'decision_question', 'material', 'viewer_action', 'experience', 'core_question', 'insight')


def plan_items(state, plan, records):
    """One work plan, item by item. Returns the plan values, or None when the plan was dropped."""
    config = state['config']
    research = state['research']
    decision = next(d for d in research['decisions'] if d['id'] == plan['decision_id'])
    insight = next(i for i in research['insights'] if i['id'] == decision['insight_ids'][0])
    claims = [c['statement'] for c in research['claims'] if c['id'] in insight['claim_ids']]
    pid = plan['id']
    work = {'central_proposition': research['proposition'], 'decision_question': decision['question'],
            'selected_option': decision['selected_option'], 'insight': insight['statement'], 'claims': claims}
    others = [p for p in records['plans'] if p['id'] != pid]
    values = {}
    threshold = config['similarity_threshold']

    def item(name, instruction, extra=None, keep=(), **kwargs):
        optional = tuple(k for k in WORK_OPTIONAL if k not in keep)
        value = yield from ask(state, f'plan.{pid}.{name}', instruction, {**work, **(extra or {})}, optional, **kwargs)
        values[name] = value
        return value

    if (yield from item('title', 'この作品案の題を1つ、短く返してください。',
                        {'other_titles': [p['title'] for p in others], 'similarity_threshold': threshold},
                        max_chars=40, checks=['not_similar_to:other_titles'])) is None:
        return None
    work['title'] = values['title']
    if (yield from item('proposition', '採用された選択肢（selected_option）を作品にするとき、この作品案が示す命題を1文で返してください。',
                        {'other_propositions': [p['proposition'] for p in others], 'similarity_threshold': threshold},
                        min_chars=15, max_chars=160, checks=['one_sentence', 'not_similar_to:other_propositions'])) is None:
        return None
    work['proposition'] = values['proposition']
    if (yield from item('core-question', 'この作品案の核になる問いを1文で返してください。',
                        max_chars=120, checks=['ends_with_question', 'one_sentence'])) is None:
        return None
    work['core_question'] = values['core-question']
    if (yield from item('experience', '鑑賞者がこの作品で経験することを、具体的に1文で返してください。',
                        min_chars=15, max_chars=200, checks=['one_sentence'])) is None:
        return None
    work['experience'] = values['experience']
    components = []
    for index in range(1, config['components_per_plan'] + 1):
        value = yield from item(f'component.{index}', 'この作品を構成する要素（物・映像・指示書・場所など）を1つ、既出（previous_components）と異なるものとして具体的に返してください。',
                                {'previous_components': list(components), 'similarity_threshold': threshold},
                                max_chars=120, checks=['not_similar_to:previous_components'])
        if value is None:
            return None
        components.append(value)
    work['components'] = components
    if (yield from item('viewer-action', '鑑賞者がこの作品の前でとる行為を1文で返してください。',
                        min_chars=10, max_chars=200, checks=['one_sentence'])) is None:
        return None
    work['viewer_action'] = values['viewer-action']
    if (yield from item('material', '主な素材と、その素材の振る舞い（時間とともにどう変わるか、何に応答するか）を1文で返してください。',
                        min_chars=15, max_chars=200, checks=['one_sentence'])) is None:
        return None
    work['material'] = values['material']
    if (yield from item('leap', '調査の洞察（insight）から一歩踏み出して、この案が新しく提案すること（飛躍）を1文で返してください。洞察の言い換えにしないでください。',
                        {'similarity_threshold': threshold}, keep=('insight',),
                        min_chars=15, max_chars=200, checks=['one_sentence', 'not_similar_to:insight'])) is None:
        return None
    # Optional: an abandoned "exclude" leaves the list empty, which the schema allows.
    yield from item('exclude', 'この案に意図して入れないものを1つ、短く返してください。', max_chars=80)
    precedents = [{'title': p['work_title'], 'difference': p['difference']}
                  for p in precedents_for(research, decision['id'])[:2]]
    read = [s['title'] for s in research['sources'] if s['id'] in decision['source_ids']][:3]
    if (yield from item('difference', '先行作品（precedents）または調査で読んだ資料（sources_read）と比べた、この案の違いを1文で返してください。資料にない事実は加えないでください。',
                        {'precedents': precedents, 'sources_read': read},
                        min_chars=20, max_chars=200, checks=['one_sentence'])) is None:
        return None
    steps = []
    for index in range(1, config['steps_per_plan'] + 1):
        step = yield from item(f'step.{index}', f'この案を作る段取りの{index}番目を1文で返してください。既出の段取り（previous_steps）の次に行うことを書いてください。',
                               {'previous_steps': [s['title'] for s in steps], 'similarity_threshold': threshold},
                               min_chars=10, max_chars=160, checks=['one_sentence', 'not_similar_to:previous_steps'])
        if step is None:
            return None
        done = yield from ask(state, f'plan.{pid}.step-done.{index}', 'この段取りが終わったと確かめられる条件を1文で返してください。',
                              {'work_title': work['title'], 'step': step}, min_chars=10, max_chars=160, checks=['one_sentence'])
        if done is None:
            return None
        effect = yield from ask(state, f'plan.{pid}.step-effect.{index}',
                                'この段取りが外に及ぼす影響の種類を1つ選んでください。READ_ONLY=読む・調べるだけ、REPOSITORY_WRITE=ファイルを作る・書く、'
                                'PHYSICAL_EXTERNAL=物を作る・設置する・人に頼む、PUBLICATION=公開する、PURCHASE=購入する。',
                                {'step': step}, choices=config['effect_types'])
        if effect is None:
            return None
        steps.append({'title': step, 'completion_condition': done, 'effect_type': effect})
    if (yield from item('technical', 'この案を技術的に実現する見通しを1文で返してください。',
                        min_chars=10, max_chars=200, checks=['one_sentence'])) is None:
        return None
    for name, label, choices in (('cost', '費用の規模', 'cost_bands'), ('duration', '制作期間の規模', 'duration_bands'),
                                 ('venue', '会場への依存の度合い', 'venue_dependency')):
        if (yield from item(name, f'この案の{label}を1つ選んでください。', choices=config[choices])) is None:
            return None
    if (yield from item('uncertainty', 'この案で、試作して確かめないと分からないことを1文で返してください。',
                        min_chars=10, max_chars=200, checks=['one_sentence'])) is None:
        return None
    return {'id': pid, 'decision': decision, 'insight': insight, 'title': values['title'],
            'proposition': values['proposition'], 'core_question': values['core-question'],
            'experience': values['experience'], 'components': components, 'viewer_action': values['viewer-action'],
            'material': values['material'], 'leap': values['leap'], 'exclude': values['exclude'],
            'difference': values['difference'], 'steps': steps, 'technical': values['technical'],
            'cost': values['cost'], 'duration': values['duration'], 'venue': values['venue'],
            'uncertainty': values['uncertainty']}


def compare(plans):
    """Rate plans on recorded facts only and recommend one (program, not inference)."""
    scale = {'NONE': 0, 'LOW': 0, 'MEDIUM': 1, 'HIGH': 2, 'HOURS': 0, 'DAYS': 0, 'WEEKS': 1, 'MONTHS': 2}
    rows = []
    for plan in plans:
        evidence = len(plan['decision']['evidence_ids'])
        claims = len(plan['insight']['claim_ids'])
        burden = scale[plan['cost']] + scale[plan['duration']] + scale[plan['venue']]
        rows.append({'plan': plan, 'evidence': evidence, 'claims': claims,
                     'grounding': min(5, 1 + evidence + claims), 'feasibility': max(1, 5 - burden)})
    best = max(rows, key=lambda r: (r['grounding'] + r['feasibility'], -int(r['plan']['id'][2:])))
    axes = [
        {'name': '根拠の厚さ', 'assessments': [
            {'hypothesis_id': r['plan']['id'], 'rating': r['grounding'],
             'statement': f"判断 {r['plan']['decision']['id']} は根拠 {r['evidence']} 件と、洞察 {r['plan']['insight']['id']} の主張 {r['claims']} 件に基づく。"}
            for r in rows]},
        {'name': '対立の扱い', 'assessments': [
            {'hypothesis_id': r['plan']['id'],
             'statement': (f"洞察 {r['plan']['insight']['id']} には対立する主張（{', '.join(r['plan']['insight']['opposing_claim_ids'])}）が残る。"
                           if r['plan']['insight']['opposing_claim_ids'] else
                           f"洞察 {r['plan']['insight']['id']} に対立する主張の記録はない。")}
            for r in rows]},
        {'name': '実現性', 'assessments': [
            {'hypothesis_id': r['plan']['id'], 'rating': r['feasibility'],
             'statement': f"費用 {r['plan']['cost']}・期間 {r['plan']['duration']}・会場依存 {r['plan']['venue']}。"}
            for r in rows]},
    ]
    reason = (f"{best['plan']['id']}「{best['plan']['title']}」を推薦する。根拠の厚さ {best['grounding']}/5 と"
              f"実現性 {best['feasibility']}/5 の合計が最も高い（同点なら番号の若い案）。")
    return best['plan'], axes, reason


def key_points(state, plan, records):
    """Key points of the recommended plan, one value per request."""
    config = state['config']
    pid = plan['id']
    work = {'work_title': plan['title'], 'proposition': plan['proposition'], 'core_question': plan['core_question'],
            'experience': plan['experience'], 'viewer_action': plan['viewer_action'], 'material': plan['material']}
    optional = ('material', 'viewer_action', 'experience', 'core_question')
    values = {}
    threshold = config['similarity_threshold']

    def point(name, instruction, extra=None, **kwargs):
        value = yield from ask(state, f'point.{pid}.{name}', instruction, {**work, **(extra or {})}, optional, **kwargs)
        values[name] = value
        return value

    scenes = []
    for index in range(1, config['encounter_scenes'] + 1):
        scenes.append((yield from point(f'encounter.{index}', '鑑賞者がこの作品に出会う場面を1つ、既出（previous_scenes）と異なる具体的な場面として1文で返してください。',
                                        {'previous_scenes': list(scenes), 'similarity_threshold': threshold},
                                        min_chars=10, max_chars=200, checks=['one_sentence', 'not_similar_to:previous_scenes'])))
    yield from point('position', '鑑賞者が作品に対してどこにいるか（位置・距離・姿勢）を1文で返してください。',
                     min_chars=5, max_chars=160, checks=['one_sentence'])
    yield from point('first-seconds', '鑑賞者が最初の数秒で見るもの・気づくことを1文で返してください。',
                     min_chars=10, max_chars=200, checks=['one_sentence'])
    yield from point('after-30s', '30秒後に鑑賞者が気づくこと・することを1文で返してください。',
                     {'first_seconds': values['first-seconds']}, min_chars=10, max_chars=200, checks=['one_sentence'])
    yield from point('after-3min', '3分後に鑑賞者に残ること・考えることを1文で返してください。',
                     {'after_30s': values['after-30s']}, min_chars=10, max_chars=200, checks=['one_sentence'])
    yield from point('field', 'この作品が属する分野を短く返してください。', max_chars=40)
    yield from point('stands-against', 'この作品が対抗するもの（考え方・慣習・制度など）を1文で返してください。',
                     min_chars=10, max_chars=200, checks=['one_sentence'])
    yield from point('claim', 'この作品の主張を1文で返してください。', min_chars=15, max_chars=200, checks=['one_sentence'])
    yield from point('who-disagrees', 'この主張に反対する立場の人を、具体的に1つ返してください。',
                     {'claim': values['claim']}, min_chars=3, max_chars=120, checks=['not_one_of:' + NOBODY])
    yield from point('denies', 'この作品が否定するものを1文で返してください。',
                     {'claim': values['claim']}, min_chars=10, max_chars=200, checks=['one_sentence'])
    yield from point('shown-not-told', 'この作品が言葉で説明せずに見せるものを1文で返してください。',
                     {'claim': values['claim']}, min_chars=15, max_chars=200, checks=['one_sentence'])
    # The mechanism and its judgement are separate values. "Merely plainer" re-asks only the
    # mechanism; the plan and every other key point are kept (design section 6).
    rounds = []
    for number in range(1, config['mechanism_rounds'] + 1):
        extra = {'claim': values['claim'], 'components': plan['components']}
        instruction = 'この作品の仕掛け（どの技術・手順で、鑑賞者に何が起きるか）を30文字以上で返してください。'
        checks = []
        if rounds:
            # Compared with every earlier mechanism, so a later round cannot repeat the first one.
            extra.update(previous_mechanism=rounds[-1]['mechanism'], judgement_reason=rounds[-1]['reason'],
                         earlier_mechanisms=[r['mechanism'] for r in rounds], similarity_threshold=threshold)
            instruction = ('前の仕掛け（previous_mechanism）は、技術を外しても地味になるだけと判定されました（judgement_reason）。'
                           '技術を外すと作品が成り立たなくなる仕掛けに作り直し、30文字以上で返してください。')
            checks = ['not_similar_to:earlier_mechanisms']
        mechanism = yield from point(f'mechanism.{number}', instruction, extra, min_chars=30, max_chars=300, checks=checks)
        verdict = yield from ask(state, f'point.{pid}.without-technique.{number}',
                                 'この仕掛けの技術を外したとき、作品は成り立たなくなりますか（はい）、それとも地味になるだけで成り立ちますか（いいえ）。理由を1文で添えてください。',
                                 {'work_title': plan['title'], 'claim': values['claim'], 'mechanism': mechanism},
                                 boolean=True, checks=['one_sentence'])
        rounds.append({'element_id': f'point.{pid}.mechanism.{number}', 'mechanism': mechanism,
                       'judgement': CEASES if verdict['answer'] else PLAINER, 'reason': verdict['reason']})
        if verdict['answer']:
            break
    values['mechanism'] = rounds[-1]['mechanism']
    values['without_the_technique'] = rounds[-1]['judgement']
    records['mechanism_rounds'] = rounds
    return values


def prototype(state, plan):
    """The recommended plan's prototype question and method."""
    pid = plan['id']
    question = yield from ask(state, f'prototype.{pid}.question', 'その分からないこと（uncertainty）を確かめる試作の問いを1文で返してください。',
                              {'work_title': plan['title'], 'uncertainty': plan['uncertainty']},
                              max_chars=120, checks=['ends_with_question', 'one_sentence'])
    method = yield from ask(state, f'prototype.{pid}.method', 'その試作の問い（question）を確かめる方法を1文で返してください。',
                            {'work_title': plan['title'], 'components': plan['components'], 'question': question},
                            ('components',), min_chars=10, max_chars=200, checks=['one_sentence'])
    return {'question': question, 'method': method}


def requirements(state, plan, points, records):
    """Requirements of the recommended plan with one acceptance test each."""
    config = state['config']
    material = {
        'experience': {'experience': plan['experience'], 'first_seconds': points['first-seconds'],
                       'after_30s': points['after-30s'], 'after_3min': points['after-3min']},
        'material': {'material': plan['material'], 'components': plan['components']},
        'interaction': {'viewer_action': plan['viewer_action'], 'position': points['position']},
        'mechanism': {'mechanism': points['mechanism'], 'without_the_technique': points['without_the_technique']},
    }
    for index, entry in enumerate(config['requirement_categories'], 1):
        category, label = entry['id'], entry['label']
        rid, tid = f'RQ{index:03}', f'AT{index:03}'
        statement = yield from ask(state, f'requirement.{plan["id"]}.{category}',
                                   f'この作品が必ず満たすこと（{label}）を、完成後に確かめられる形で1文で返してください。',
                                   {'work_title': plan['title'], 'proposition': plan['proposition'], **material.get(category, {})},
                                   ('components',), min_chars=15, max_chars=200, checks=['one_sentence'])
        condition = yield from ask(state, f'acceptance.{rid}.pass-condition',
                                   'この要件が満たされたと判定する合格条件（何を見て、どうなっていれば合格か）を1文で返してください。',
                                   {'requirement': statement}, min_chars=15, max_chars=200, checks=['one_sentence'])
        method = yield from ask(state, f'acceptance.{rid}.method',
                                'この合格条件を確かめる方法を1つ選んでください。observation=作品を観察する、prototype_review=試作を点検する、'
                                'viewer_session=鑑賞者に体験してもらう、measurement=測る。',
                                {'requirement': statement, 'pass_condition': condition}, choices=config['acceptance_methods'])
        records['requirements'].append({'id': rid, 'category': category, 'statement': statement,
                                        'source_decisions': [plan['decision']['id']], 'priority': 'mandatory',
                                        'acceptance_test_ids': [tid], 'status': 'PROPOSED'})
        records['acceptance_tests'].append({'id': tid, 'target_requirement': rid, 'method': method,
                                            'preconditions': '試作 PP001 の段取りが終わっている。',
                                            'pass_condition': condition, 'evidence_to_record': f'{method} の記録（{tid}）',
                                            'executor': 'production-validator', 'result': 'NOT_RUN'})


def cited_evidence(research, plan):
    """Evidence the handoff will cite for this plan: the decision's and its insight's claims' evidence."""
    ids = set(plan['decision']['evidence_ids'])
    for claim in research['claims']:
        if claim['id'] in plan['insight']['claim_ids']:
            ids.update(claim['evidence_ids'])
    return [e for e in research.get('evidence', []) if e['id'] in ids]


def references(state, plan, records):
    """Which cited evidence record answers each production reference category (one choice each)."""
    research = state['research']
    titles = {s['id']: s['title'] for s in research['sources']}
    cited = cited_evidence(research, plan)
    if not cited:
        return
    for length in (160, 80, 40, 0):
        # Shorter quotes before anything else: every cited record must stay choosable.
        sources = [{'id': e['id'], 'title': titles.get(e['source_id'], ''), 'url': e['url'],
                    **({'quote': e['quote'][:length]} if length and e.get('quote') else {})} for e in cited]
        if len(json.dumps(sources, ensure_ascii=False).encode()) <= state['config']['input_bytes'] - 600:
            break
    for entry in state['config'].get('reference_categories') or []:
        category = entry['id']
        value = yield from ask(state, f'reference.{category}',
                               f'制作のための参考資料として「{entry["label"]}」に当たるものを、資料（sources）から1つ選んで ID を返してください。'
                               '当たる資料が無ければ NONE を返してください。',
                               {'work_title': plan['title'], 'mechanism': records['points']['mechanism'], 'sources': sources},
                               ('mechanism',), choices=[*[s['id'] for s in sources], 'NONE'])
        if value and value != 'NONE':
            records['references'].setdefault(value, []).append(category)


def schedule(state, records):
    for plan in state['plans']:
        values = yield from plan_items(state, plan, records)
        if values is None:
            records['dropped_plans'].append(plan['id'])
        else:
            records['plans'].append(values)
    recommended, axes, reason = compare(records['plans'])
    records.update(recommended=recommended, axes=axes, reason=reason)
    records['points'] = yield from key_points(state, recommended, records)
    records['prototype'] = yield from prototype(state, recommended)
    yield from requirements(state, recommended, records['points'], records)
    yield from references(state, recommended, records)


def advance(state):
    records = {'plans': [], 'dropped_plans': [], 'requirements': [], 'acceptance_tests': [], 'references': {}}
    pending = None
    for request in schedule(state, records):
        if request['element_id'] not in state['answers'] and request['element_id'] not in state.get('abandoned', {}):
            pending = request
            break
    if state['status'] != 'BLOCKED':
        state.update(pending=pending, status='WAITING' if pending else 'COMPLETED')
    state['record_counts'] = {'plans': len(records['plans']), 'dropped_plans': records['dropped_plans'],
                              'requirements': len(records['requirements']),
                              'acceptance_tests': len(records['acceptance_tests'])}
    if state['status'] == 'COMPLETED':
        state['files'] = assemble(state, records)
        state['recommended_hypothesis_id'] = records['recommended']['id']
        state['mechanism_rounds'] = records['mechanism_rounds']


# ---- Assembly (program only) ----

def transcribed(research, decision_id):
    """Key points taken from Stage B records instead of asked for (design section 6)."""
    sources = research['sources']
    independent = [s for s in sources if not s['duplicate_of']]
    counts = research['counts']
    titles = '、'.join(f"「{s['title']}」" for s in independent[:5])
    open_items = []
    if research['question_gaps']:
        open_items.append(f"未解決の問い {len(research['question_gaps'])} 件（{', '.join(research['question_gaps'])}）")
    if counts.get('contradictions'):
        open_items.append(f"対立したままの主張の組 {counts['contradictions']} 件")
    if research['quality_failures']:
        open_items.append('調査の下限に届かない項目: ' + '、'.join(research['quality_failures']))
    open_items.append(f"試作と鑑賞者による確認は未実施（調査の不確実性 {counts.get('uncertainties', 0)} 件が OPEN）")
    scan = research['repetition_scan'] or {}
    risk = scan.get('risk_level', 'UNKNOWN')
    refs = sorted({m.get('prior_signal_ref') for m in scan.get('matches', []) if m.get('prior_signal_ref')})
    return {
        'questions': [q['question'] for q in research['questions']],
        'what_was_read': (f"取得資料 {len(sources)} 件（内容が重複しない資料 {len(independent)} 件）から、"
                          f"根拠 {counts.get('evidence', 0)} 件・抜き書き {counts.get('excerpts', 0)} 件を取った。" + (f"主な資料: {titles}。" if titles else '')),
        'what_came_out': (f"主張 {counts.get('claims', 0)} 件、洞察 {counts.get('insights', 0)} 件、判断 {counts.get('decisions', 0)} 件。"
                          + ' '.join(f"{i['id']}: {i['statement']}" for i in research['insights'][:3])),
        'what_is_not_settled': '。'.join(open_items) + '。',
        'precedents': [{'reference': f"{p['work_title']} <{p['source_url']}>", 'difference': p['difference']}
                       for p in precedents_for(research, decision_id)],
        'self_repetition_risk': {
            'reference': ', '.join(refs) if refs else (
                'creator history not supplied (Stage B --history-root)' if risk == 'UNKNOWN' else 'pinned creator history'),
            'assessment': f"{risk}: {scan.get('assessment') or 'Creator history was not supplied; repetition is unmeasured.'}"},
    }


def digital_prototype(plan, uncertainty_id, tests, step_count):
    """The local digital prototype production requires (program, no inference, no physical effect).

    It reads the production plan's dimensions, materials and quantity and draws the components as an
    SVG; agentic-art-production `build_prototype.py` renders it. Physical steps stay in PP001.
    """
    first = step_count + 1
    return {
        'id': 'PP002', 'hypothesis_id': plan['id'],
        'question': f"制作プランの寸法・素材・数量から描いた図で、「{plan['title']}」の構成要素の配置と大きさを確かめられるか？",
        'uncertainty_ids': [uncertainty_id],
        'method': '制作プランの寸法・素材・数量を読み、構成要素の配置を SVG の図に描いて、制作要件と照らす。',
        'inputs': ['03_plan/production-plan.yaml dimensions（寸法）', '03_plan/production-plan.yaml materials（素材）',
                   '03_plan/production-plan.yaml quantity（数量）', *plan['components']],
        'constraints': ['物の制作・購入・公開・依頼をしない（READ_ONLY と REPOSITORY_WRITE の段取りだけ）。'],
        'tasks': [
            {'id': f'PT{first:03}', 'title': '制作プランの寸法・素材・数量を読み、描く構成要素を並べる。', 'depends_on': [],
             'completion_condition': '構成要素ごとに寸法・素材・数量が一覧になっている。', 'effect_type': 'READ_ONLY'},
            {'id': f'PT{first + 1:03}', 'title': '構成要素の配置を SVG の図に描いて試作の出力として保存する。', 'depends_on': [f'PT{first:03}'],
             'completion_condition': 'SVG の図が保存され、すべての構成要素が寸法の比率どおりに描かれている。', 'effect_type': 'REPOSITORY_WRITE'}],
        'acceptance_test_ids': list(tests),
        'expected_evidence': '構成要素の配置と寸法を描いた SVG 画像（描画による試作で、物の制作ではない）。',
        'estimated_cost_band': 'LOW', 'estimated_duration_band': 'HOURS',
        'executor_capability': 'digital-prototype-renderer', 'status': 'PLANNED'}


def dump(key, rows):
    return yaml.safe_dump({key: rows}, allow_unicode=True, sort_keys=False)


def assemble(state, records):
    research = state['research']
    config = state['config']
    paths = config['paths']
    plans, recommended, points = records['plans'], records['recommended'], records['points']
    info = transcribed(research, recommended['decision']['id'])
    tests = [t['id'] for t in records['acceptance_tests']]
    hypotheses = []
    for index, plan in enumerate(plans):
        chosen = plan is recommended
        hypotheses.append({
            'id': plan['id'], 'title': plan['title'], 'proposition': plan['proposition'],
            'source_decision_ids': [plan['decision']['id']], 'source_insight_ids': [plan['insight']['id']],
            'intended_experience': list(dict.fromkeys([plan['experience'], plan['viewer_action']])),
            'includes': list(dict.fromkeys([*plan['components'], plan['material']])),
            'excludes': [plan['exclude']] if plan['exclude'] else [],
            'differentiation': {'precedent_refs': [], 'statement': plan['difference']},
            'feasibility': {'technical': plan['technical'], 'cost_band': plan['cost'], 'duration_band': plan['duration'],
                            'venue_dependency': plan['venue'], 'rights_status': 'REVIEW_REQUIRED', 'safety_status': 'REVIEW_REQUIRED'},
            # Hypothesis uncertainties are registered beside the Stage B register; number after it.
            'uncertainties': [{'id': f"U{research['max_uncertainty'] + index + 1:03}", 'statement': plan['uncertainty'],
                               'severity': 'MAJOR', 'prototype_plan_ids': ['PP001'] if chosen else [],
                               'external_validation_reason': None if chosen else
                               '推薦されなかった案のため試作計画は作っていない。この案を選び直すときに試作を計画する。'}],
            'recommendation': 'RECOMMENDED' if chosen else 'ALTERNATIVE',
            **({'single_hypothesis_rationale': '比較できる別の作品案が成立しなかったため、1案のみを扱う。'} if len(plans) == 1 else {}),            'status': 'PROPOSED'})
    chosen_uid = next(h['uncertainties'][0]['id'] for h in hypotheses if h['id'] == recommended['id'])
    prototypes = [{
        'id': 'PP001', 'hypothesis_id': recommended['id'], 'question': records['prototype']['question'],
        'uncertainty_ids': [chosen_uid], 'method': records['prototype']['method'], 'inputs': list(recommended['components']),
        'constraints': [], 'tasks': [{'id': f'PT{n + 1:03}', 'title': s['title'], 'depends_on': [f'PT{n:03}'] if n else [],
                                      'completion_condition': s['completion_condition'], 'effect_type': s['effect_type']}
                                     for n, s in enumerate(recommended['steps'])],
        'acceptance_test_ids': tests,
        'expected_evidence': f"試作の問い「{records['prototype']['question']}」への答えと、各段取りの完了条件を満たした記録。",
        'estimated_cost_band': recommended['cost'], 'estimated_duration_band': recommended['duration'],
        'executor_capability': 'production-agent（PHYSICAL_EXTERNAL・PUBLICATION・PURCHASE の段取りは人の承認後に実行）',
        'status': 'PLANNED'}]
    if config.get('digital_prototype_plan'):
        prototypes.append(digital_prototype(recommended, chosen_uid, tests, len(recommended['steps'])))
        for hypothesis in hypotheses:
            if hypothesis['id'] == recommended['id']:
                hypothesis['uncertainties'][0]['prototype_plan_ids'].append('PP002')
    comparisons =[] if len(plans) < 2 else [{
        'id': 'HC001', 'hypothesis_ids': [p['id'] for p in plans], 'axes': records['axes'],
        'recommended_hypothesis_id': recommended['id'], 'reason': records['reason'], 'status': 'COMPLETE'}]
    brief = {
        'schema_version': '1.0.0', 'brief_id': 'PB001', 'revision': 1,
        'completion_image': {'encounter': [points[f'encounter.{i}'] for i in range(1, config['encounter_scenes'] + 1)],
                             'position': points['position'], 'first_seconds': points['first-seconds'],
                             'after_30s': points['after-30s'], 'after_3min': points['after-3min']},
        'theme': {'field': points['field'], 'stands_against': points['stands-against'],
                  'difference': recommended['difference'],
                  # Transcribed, not judged: the dated research finding this plan answers.
                  'why_now': (f"{research['acquired_through']} 時点の公開資料の調査で、この案の元になった洞察"
                              f"（{recommended['insight']['id']}）が得られた: {recommended['insight']['statement']}")},
        'message': {'claim': points['claim'], 'who_disagrees': points['who-disagrees'],
                    'denies': points['denies'], 'shown_not_told': points['shown-not-told']},
        'concept': {'mechanism': points['mechanism'], 'without_the_technique': points['without_the_technique'],
                    'precedents': info['precedents'], 'self_repetition_risk': info['self_repetition_risk']},
        'research_summary': {'questions': info['questions'], 'what_was_read': info['what_was_read'],
                             'what_came_out': info['what_came_out'], 'what_is_not_settled': info['what_is_not_settled']},
    }
    requirement_lines = '\n'.join(f"- {r['id']}（{r['category']}）: {r['statement']}" for r in records['requirements'])
    alternatives = '\n'.join(f"- {p['id']}「{p['title']}」: {p['proposition']}" for p in plans if p is not recommended)
    direction = (
        f"# {research['title']} — Creative direction\n\n"
        f"## 作品の核\n\n{recommended['id']}「{recommended['title']}」: {recommended['proposition']}\n\n"
        f"核の問い: {recommended['core_question']}\n\n"
        f"## 継承する美的傾向\n\n素材と振る舞い: {recommended['material']}\n\n構成要素: {'、'.join(recommended['components'])}\n\n"
        f"## 意図的に破る傾向\n\n{points['denies']}\n\n飛躍: {recommended['leap']}\n\n"
        f"## 必須制約\n\n{requirement_lines}\n\n"
        f"仕掛け（{points['without_the_technique']}）: {points['mechanism']}\n\n"
        f"## 未解決事項\n\n{info['what_is_not_settled']}\n"
        + (f"\n## 比較した別の案\n\n{alternatives}\n\n{records['reason']}\n" if alternatives else ''))
    files = {
        paths['hypotheses']: dump('hypotheses', hypotheses),
        paths['comparison']: dump('comparisons', comparisons),
        paths['requirements']: dump('requirements', records['requirements']),
        paths['acceptance_tests']: dump('acceptance_tests', records['acceptance_tests']),
        paths['prototype_plans']: dump('prototype_plans', prototypes),
        paths['creative_direction']: direction,
        paths['brief']: yaml.safe_dump(brief, allow_unicode=True, sort_keys=False),
    }
    if paths.get('reference_categories'):
        files[paths['reference_categories']] = yaml.safe_dump(
            {'categories': {key: records['references'][key] for key in sorted(records['references'])}},
            allow_unicode=True, sort_keys=False)
    # Every answered value already passed the forbidden-word check. Transcribed values are checked
    # here: a field that is empty or only a template placeholder is never written.
    unfilled = [f'{name}:{field}' for name, value in (('hypotheses', hypotheses), ('prototype_plans', prototypes),
                                                      ('comparisons', comparisons), ('brief', brief),
                                                      ('requirements', records['requirements']),
                                                      ('acceptance_tests', records['acceptance_tests']))
                for field, text in strings(value) if placeholder(text)]
    if unfilled:
        raise ValueError('Stage C output has an unfilled field: ' + ', '.join(unfilled))
    return files


PLACEHOLDERS = {'', '未記入', '未確定', '無し', 'なし', 'tbd', 'todo', 'n/a'}


def placeholder(text):
    return text.strip().rstrip('。.').strip().casefold() in PLACEHOLDERS


def strings(value, path='$'):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from strings(child, f'{path}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from strings(child, f'{path}[{index}]')
