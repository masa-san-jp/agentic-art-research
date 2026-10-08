"""Single-value wire protocol, retries and recoverable deterministic projections."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from _common import load_json
from new_project import create_project
from research_elements import Engine

NOW = '2026-10-08T00:00:00Z'

class ResearchElementsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = create_project(Path(self.temp.name), 'elements', 'Elements', protocol_root=ROOT, created_at=NOW)
        (self.project / '00_intake/creative-intent.md').write_text('How does a deferred choice shape a shared image?')
        self.engine = Engine(self.project)

    def reply(self, request, value):
        return {**{k: request[k] for k in ('run_id', 'element_id', 'attempt')},
                'contract_version': 'element-answer/v1', 'value': value}

    def test_retry_only_failed_element_and_replay(self):
        report = self.engine.next(now=NOW)
        request = report['next_action']['request']
        self.assertEqual('element-request/v1', request['contract_version'])
        bad = self.reply(request, '')
        retry = self.engine.answer(bad, now=NOW)['next_action']['request']
        self.assertEqual(request['element_id'], retry['element_id'])
        self.assertEqual(2, retry['attempt'])
        self.assertTrue(retry['previous_failure'])
        answer = self.reply(retry, 'How does a deferred choice affect shared images?')
        next_report = self.engine.answer(answer, now=NOW)
        self.assertEqual(next_report, self.engine.answer(answer, now=NOW))
        self.assertEqual(next_report, self.engine.next(now=NOW))
        rows = yaml.safe_load((self.project / '01_planning/question-register.yaml').read_text())['questions']
        self.assertEqual(1, len(rows))

    def test_wrong_identity_does_not_consume_attempt(self):
        request = self.engine.next(now=NOW)['next_action']['request']
        bad = self.reply(request, 'What is shared?')
        bad['attempt'] = 2
        with self.assertRaisesRegex(ValueError, 'identity/attempt'):
            self.engine.answer(bad, now=NOW)
        self.assertEqual(request, self.engine.next(now=NOW)['next_action']['request'])

    def test_retry_exhaustion_blocks_with_named_failure(self):
        report = self.engine.next(now=NOW)
        for _ in range(5):
            request = report['next_action']['request']
            report = self.engine.answer(self.reply(request, ''), now=NOW)
        self.assertEqual('BLOCKED', report['status'])
        self.assertIsNone(report['next_action'])
        self.assertIn('non_empty', [f['check'] for f in report['blocked']['last_failure']])

    def test_input_and_foreign_output_changes_fail_closed(self):
        self.engine.next(now=NOW)
        path = self.project / '03_knowledge/claims.jsonl'
        path.write_text('{"foreign":true}\n')
        with self.assertRaisesRegex(ValueError, 'output changed'):
            self.engine.next(now=NOW)
        path.write_text('')
        (self.project / '00_intake/creative-intent.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.engine.next(now=NOW)

    def test_projection_failure_recovers_from_saved_answer(self):
        request = self.engine.next(now=NOW)['next_action']['request']
        original = self.engine.materialize
        self.engine.materialize = lambda state: (_ for _ in ()).throw(OSError('interrupted'))
        # Initial preflight happens before answer save; interrupt only post-save.
        calls = 0
        def interrupted(state):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('interrupted')
            original(state)
        self.engine.materialize = interrupted
        with self.assertRaisesRegex(OSError, 'interrupted'):
            self.engine.answer(self.reply(request, 'How does a deferred choice affect shared images?'), now=NOW)
        self.engine.materialize = original
        report = self.engine.next(now=NOW)
        self.assertEqual(1, report['accepted_count'])
        self.assertIn('Q001', (self.project / '01_planning/question-register.yaml').read_text())

    def test_canonical_and_symlink_projects_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside Git'):
            Engine(ROOT)
        link = Path(self.temp.name) / 'link'
        link.symlink_to(self.project)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            Engine(link)

    def small_plan(self, **budget):
        path = self.project / '01_planning/research-plan.yaml'
        plan = yaml.safe_load(path.read_text())
        plan['element_research'] = {'question_count': 1, 'search_limit': 2}
        plan['minimums'] = {'evidence': 2, 'claims': 2, 'insights': 1, 'decisions': 1,
                            'requirements': 0, 'reason': 'Synthetic bounded exploratory qualification.'}
        plan['budget'].update(budget)
        path.write_text(yaml.safe_dump(plan, sort_keys=False))

    def fake(self, request):
        import importlib.util
        spec = importlib.util.spec_from_file_location('fake_research', ROOT / 'tests/fixtures/workers/fake_research_answerer.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.answer(request)

    def until_search(self):
        self.small_plan()
        report = self.engine.next(now=NOW)
        while report['next_action']['request']['contract_version'] != 'search-request/v1':
            report = self.engine.answer(self.fake(report['next_action']['request']), now=NOW)
        return report['next_action']['request']

    def test_search_hash_dedup_and_exact_excerpt_retry(self):
        import hashlib
        request = self.until_search()
        answer = self.fake(request)
        answer['results'][1]['body'] = answer['results'][0]['body']
        report = self.engine.answer(answer, now=NOW)
        request = report['next_action']['request']
        self.assertTrue(request['element_id'].startswith('relevance.'))
        self.assertEqual({'question', 'title', 'body'}, set(request['inputs']))
        report = self.engine.answer(self.fake(request), now=NOW)
        request = report['next_action']['request']
        report = self.engine.answer(self.reply(request, 'Changed quotation.'), now=NOW)
        self.assertEqual(2, report['next_action']['request']['attempt'])
        self.assertEqual(request['element_id'], report['next_action']['request']['element_id'])
        self.assertIn('exact_excerpt:source_url', [f['check'] for f in report['next_action']['request']['previous_failure']])
        request = report['next_action']['request']
        self.engine.answer(self.fake(request), now=NOW)
        ledger = [json.loads(line) for line in (self.project / '02_evidence/source-ledger.jsonl').read_text().splitlines()]
        self.assertEqual(2, len(ledger))
        self.assertEqual(ledger[0]['url'], ledger[1]['duplicate_of'])
        self.assertEqual('sha256:' + hashlib.sha256(ledger[0]['body'].encode()).hexdigest(), ledger[0]['content_hash'])
        evidence = (self.project / '02_evidence/evidence-ledger.jsonl').read_text().splitlines()
        self.assertEqual(1, len(evidence))

    def test_empty_searches_stop_at_saturation(self):
        self.small_plan()
        report = self.engine.next(now=NOW)
        searches = 0
        while report['next_action']:
            request = report['next_action']['request']
            answer = self.fake(request)
            if request['contract_version'] == 'search-request/v1':
                searches += 1
                answer['results'] = []
            report = self.engine.answer(answer, now=NOW)
        self.assertEqual(2, searches)
        questions = yaml.safe_load((self.project / '01_planning/question-register.yaml').read_text())['questions']
        self.assertEqual('UNRESOLVED', questions[0]['status'])
        self.assertEqual('evidence_saturation', questions[0]['terminal_reason'])

    def test_source_cap_and_existing_search_conflict(self):
        self.small_plan(max_total_sources=1)
        report = self.engine.next(now=NOW)
        while report['next_action']['request']['contract_version'] != 'search-request/v1':
            report = self.engine.answer(self.fake(report['next_action']['request']), now=NOW)
        request = report['next_action']['request']
        self.assertEqual(1, request['limit'])
        report = self.engine.answer(self.fake(request), now=NOW)
        self.assertEqual(2, report['next_action']['request']['attempt'])
        self.assertIn('search_limit', [f['check'] for f in report['next_action']['request']['previous_failure']])

    def test_runtime_budget_blocks_without_consuming_element(self):
        self.small_plan(max_runtime_minutes=1)
        report = self.engine.next(now=NOW)
        request = report['next_action']['request']
        report = self.engine.answer(self.fake(request), now='2026-10-08T00:01:00Z')
        self.assertEqual(report, self.engine.next(now='2026-10-08T00:01:00Z'))
        self.assertEqual('BLOCKED', report['status'])
        self.assertEqual(0, report['accepted_count'])
        self.assertEqual('runtime_budget', report['blocked']['last_failure'][0]['check'])

    def test_utf8_windows_preserve_source_and_bound_inputs(self):
        from research_element_driver import chunks
        body = ('選択を先送りする。' * 1000)
        windows = list(chunks(body, 2000))
        self.assertEqual(body, ''.join(text for text, _ in windows))
        for text, start in windows:
            self.assertLessEqual(len(text.encode()), 2000)
            self.assertEqual(text, body[start:start+len(text)])

    def test_parent_wire_schemas_are_closed_and_versioned(self):
        from research_element_contracts import CONTRACTS, validator
        for name in CONTRACTS:
            self.assertEqual(name+'/v1', validator(name).schema['$id'])
            self.assertIs(False, validator(name).schema['additionalProperties'])

    def run_fake(self, mutate=None):
        report = self.engine.next(now=NOW)
        requests = []
        while report['next_action']:
            request = report['next_action']['request']
            requests.append(request)
            answer = self.fake(request)
            if mutate:
                mutate(request, answer)
            report = self.engine.answer(answer, now=NOW)
            self.assertNotEqual('BLOCKED', report['status'], report)
        return report, requests

    def test_claim_pairs_opposition_and_caution_are_program_assembled(self):
        from search_harness import _validate
        self.small_plan()
        report, requests = self.run_fake()
        kinds = [r['element_id'].split('.')[0] for r in requests]
        self.assertEqual(['question', 'query', 'search', 'relevance', 'excerpt', 'relevance', 'excerpt',
                          'observation', 'observation', 'claim', 'claim-type', 'claim', 'claim-type', 'pair'], kinds[:14])
        def rows(path):
            return [json.loads(line) for line in (self.project / path).read_text().splitlines()]
        claims = rows('03_knowledge/claims.jsonl')
        self.assertEqual(['CL002'], claims[0]['opposing_claims'])
        self.assertEqual(['CL001'], claims[1]['opposing_claims'])
        self.assertEqual({'CONTESTED'}, {c['epistemic_status'] for c in claims})
        for name, path in [('claim', '03_knowledge/claims.jsonl'), ('observation', '03_knowledge/observations.jsonl'),
                           ('relationship', '03_knowledge/relationships.jsonl'), ('contradiction', '03_knowledge/contradictions.jsonl')]:
            for row in rows(path):
                _validate(ROOT, name, row)
        self.assertEqual(1, len(rows('03_knowledge/contradictions.jsonl')))

    def test_support_assembly_does_not_create_claim_cycles(self):
        self.small_plan()
        def supports(request, answer):
            if request['element_id'].startswith('pair.'):
                answer['value'] = 'supports'
        self.run_fake(supports)
        claims = [json.loads(line) for line in (self.project / '03_knowledge/claims.jsonl').read_text().splitlines()]
        self.assertEqual([], claims[0]['supporting_claims'])
        self.assertEqual(['CL001'], claims[1]['supporting_claims'])
        self.assertEqual('', (self.project / '03_knowledge/contradictions.jsonl').read_text())
        self.assertNotIn('VERIFIED', [c['epistemic_status'] for c in claims])

    def test_fake_pipeline_reaches_decisions_and_prior_art_with_minimal_inputs(self):
        from research_element_validation import check_project
        from _common import read_jsonl
        from search_harness import _validate
        self.small_plan()
        report, requests = self.run_fake()
        self.assertEqual('COMPLETED', report['status'])
        self.assertEqual(24, report['accepted_count'])
        self.assertFalse(report['completion']['project_completed'])
        self.assertEqual('INCOMPLETE', report['completion']['status'])  # Missing historical corpus is not a measured review.
        self.assertEqual([], check_project(self.project.resolve(), ROOT))
        for request in requests:
            if 'inputs' in request:
                self.assertLessEqual(len(json.dumps(request['inputs'], ensure_ascii=False).encode()), 4096)
            self.assertNotIn('write_targets', request)
        for key, name in [('insights', 'insight'), ('decisions', 'decision'), ('rejected_options', 'rejected-option'), ('uncertainties', 'uncertainty')]:
            filename = {'insights': 'insight-register', 'decisions': 'decision-log', 'rejected_options': 'rejected-options', 'uncertainties': 'uncertainty-register'}[key]
            for row in yaml.safe_load((self.project / f'04_decisions/{filename}.yaml').read_text())[key]:
                _validate(ROOT, name, row)
        state = load_json(self.engine.path)
        for prior in read_jsonl(self.project / '03_knowledge/prior-art.jsonl'):
            _validate(ROOT, 'prior-art', prior)
            self.assertIn(prior['source_url'], state['ledger'])
        self.assertEqual('UNKNOWN', state['repetition_scan']['risk_level'])
        self.assertTrue(all(r['material_adoption'] == 'REJECTED' for r in yaml.safe_load((self.project / '06_governance/rights-register.yaml').read_text())['rights']))

    def test_checkpoint_validator_catches_excerpt_and_ledger_mutation(self):
        from research_element_validation import check_project
        self.small_plan()
        self.run_fake()
        path = self.project / '02_evidence/excerpts.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]['quote'] = 'Invented evidence'
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        rules = {rule for rule, _ in check_project(self.project.resolve(), ROOT)}
        self.assertIn('ELEMENT-EXACT-EXCERPT', rules)
        self.assertIn('ELEMENT-PROJECTION', rules)

    def test_explicit_history_reuses_native_scan_and_pins_changes(self):
        import shutil
        history = Path(self.temp.name).resolve() / 'history'
        shutil.copytree(ROOT / 'tests/fixtures/self-repetition/history', history)
        self.small_plan()
        self.engine.next(now=NOW, history_root=history)
        report, _ = self.run_fake()
        state = load_json(self.engine.path)
        self.assertEqual('AVAILABLE', state['repetition_scan']['history_access']['status'])
        self.assertNotEqual('UNKNOWN', state['repetition_scan']['risk_level'])
        self.assertEqual(4, state['repetition_scan']['scanned_project_count'])
        self.assertEqual(1, report['completion']['counts']['self_repetition_review'])
        self.assertEqual('COMPLETE_WITH_GAPS', report['completion']['status'])
        artifact = next(history.rglob('creative-direction.md'))
        artifact.write_text(artifact.read_text()+'\nchanged')
        with self.assertRaisesRegex(ValueError, 'History inputs changed'):
            self.engine.next(now=NOW)

    def test_search_rejects_pinned_content_changes_and_credentials(self):
        request = self.until_search()
        answer = self.fake(request)
        answer['results'][0]['url'] = 'https://user:password@example.invalid/source'
        report = self.engine.answer(answer, now=NOW)
        self.assertIn('url_shape', [f['check'] for f in report['next_action']['request']['previous_failure']])
        request = report['next_action']['request']
        answer = self.fake(request)
        answer['results'][0]['body'] = 'Bearer ' + 'x'*30
        report = self.engine.answer(answer, now=NOW)
        self.assertIn('secret_output', [f['check'] for f in report['next_action']['request']['previous_failure']])
        self.assertEqual({}, load_json(self.engine.path)['ledger'])

    def test_invalid_config_and_private_rights_table_are_rejected(self):
        from research_element_validation import require_schema
        from copy import deepcopy
        config = deepcopy(self.engine.config)
        config['paths']['sources'] = '../outside.jsonl'
        with self.assertRaisesRegex(ValueError, 'const failed'):
            require_schema(ROOT, 'research-elements-config', config)
        table = Path(self.temp.name) / 'rights.json'
        table.write_text(json.dumps({'https://example.invalid/choice': {'rights_status': 'unknown', 'redistribution': 'unknown', 'sensitivity': 'PRIVATE_RAW'}}))
        with self.assertRaisesRegex(ValueError, 'const failed'):
            self.engine.next(now=NOW, rights_table=table)

    def test_legacy_artifacts_and_unfilled_proposition_are_not_overwritten(self):
        (self.project / '03_knowledge/claims.jsonl').write_text('{"id":"CL001"}\n')
        with self.assertRaisesRegex(ValueError, 'existing research cannot be overwritten'):
            self.engine.next(now=NOW)
        (self.project / '03_knowledge/claims.jsonl').write_text('')
        (self.project / '00_intake/creative-intent.md').write_text('未記入。')
        with self.assertRaisesRegex(ValueError, 'filled Stage A'):
            self.engine.next(now=NOW)

    def test_source_ids_survive_json_key_sorting_and_new_earlier_url(self):
        self.small_plan()
        path = self.project / '01_planning/research-plan.yaml'
        plan = yaml.safe_load(path.read_text())
        plan['minimums'].update(evidence=3, claims=3)
        path.write_text(yaml.safe_dump(plan))
        report = self.engine.next(now=NOW)
        searches = 0
        while report['next_action']:
            request = report['next_action']['request']
            if request['contract_version'] == 'search-request/v1':
                searches += 1
                if searches == 2:
                    answer = self.fake(request)
                    answer['results'] = [{'url': 'https://example.invalid/aaaa', 'title': 'Earlier alphabetic URL',
                                          'body': 'Collective revision allows the shared image to change after a deferred choice.'}]
                    report = self.engine.answer(answer, now=NOW)
                    pending = report['next_action']['request']
                    self.assertIn('SRC003', pending['element_id'])
                    self.assertEqual(pending, self.engine.next(now=NOW)['next_action']['request'])
                    ledger = load_json(self.engine.path)['ledger']
                    self.assertEqual('SRC001', ledger['https://example.invalid/choice']['id'])
                    self.assertEqual('SRC003', ledger['https://example.invalid/aaaa']['id'])
                    break
            report = self.engine.answer(self.fake(request), now=NOW)

    def test_search_body_conflict_retries_only_the_pending_search(self):
        self.small_plan()
        path = self.project / '01_planning/research-plan.yaml'
        plan = yaml.safe_load(path.read_text())
        plan['minimums'].update(evidence=3, claims=3)
        path.write_text(yaml.safe_dump(plan))
        report = self.engine.next(now=NOW)
        searches = 0
        while report['next_action']:
            request = report['next_action']['request']
            answer = self.fake(request)
            if request['contract_version'] == 'search-request/v1':
                searches += 1
                if searches == 2:
                    answer['results'][0]['body'] += ' Changed.'
                    report = self.engine.answer(answer, now=NOW)
                    self.assertEqual(request['element_id'], report['next_action']['request']['element_id'])
                    self.assertEqual(2, report['next_action']['request']['attempt'])
                    self.assertIn('source_conflict', [f['check'] for f in report['next_action']['request']['previous_failure']])
                    self.assertNotIn('Changed.', load_json(self.engine.path)['ledger']['https://example.invalid/choice']['body'])
                    break
            report = self.engine.answer(answer, now=NOW)

    def test_materialized_research_validates_with_existing_repository_gates(self):
        import shutil
        import subprocess
        from validate import validate_repository
        from build_graph import build_graph
        self.small_plan()
        self.run_fake()
        root = Path(self.temp.name).resolve()
        for name in ('config', 'schemas'):
            shutil.copytree(ROOT / name, root / name)
        (root / 'data').mkdir()
        findings = validate_repository(root, 'project/elements', protocol_root=ROOT)
        self.assertEqual([], [(f.rule, f.message) for f in findings])
        graph = build_graph(root)
        self.assertTrue(any(node['id'] == 'DC001' for node in graph['nodes']))
        command = [sys.executable, str(ROOT / 'tools/validate.py'), '--check',
                   '--protocol-root', str(ROOT), '--work-root', str(root),
                   '--project', 'project/elements']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        # The external CLI must exercise the additional checkpoint checks.
        path = self.project / '02_evidence/excerpts.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]['quote'] = 'Changed outside the checkpoint'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertIn('ELEMENT-', result.stdout)

    def test_long_source_yields_one_excerpt_and_claim_for_the_question(self):
        self.small_plan(max_total_sources=1)
        path = self.project / '01_planning/research-plan.yaml'
        plan = yaml.safe_load(path.read_text())
        plan['minimums'].update(evidence=1, claims=1)
        path.write_text(yaml.safe_dump(plan))
        sentence = 'Deferred choice keeps the shared image open to revision.'
        body = sentence * 100
        def long_source(request, answer):
            if request['contract_version'] == 'search-request/v1':
                answer['results'] = answer['results'][:1]
                answer['results'][0]['body'] = body
            elif request['element_id'].startswith('relevance.'):
                answer['value'] = {'answer': True, 'reason': sentence}
            elif request['element_id'].startswith('excerpt.'):
                answer['value'] = sentence
        _, requests = self.run_fake(long_source)
        self.assertEqual(1, sum(r['element_id'].startswith('relevance.') for r in requests))
        self.assertEqual(1, len((self.project / '02_evidence/excerpts.jsonl').read_text().splitlines()))
        self.assertEqual(1, len((self.project / '03_knowledge/claims.jsonl').read_text().splitlines()))
        self.assertEqual(body, load_json(self.engine.path)['ledger']['https://example.invalid/choice']['body'])

    def test_replayed_answer_cannot_bypass_absolute_runtime_budget(self):
        self.small_plan(max_runtime_minutes=1)
        request = self.engine.next(now=NOW)['next_action']['request']
        answer = self.fake(request)
        report = self.engine.answer(answer, now=NOW)
        pending_id = report['next_action']['request']['element_id']
        report = self.engine.answer(answer, now='2026-10-08T00:01:00Z')
        self.assertEqual('BLOCKED', report['status'])
        self.assertEqual(1, report['accepted_count'])
        self.assertIsNone(report['next_action'])
        self.assertEqual(pending_id, report['blocked']['element_id'])
        self.assertEqual('runtime_budget', report['blocked']['last_failure'][0]['check'])

    def test_default_four_question_pipeline_and_grounded_decision_inputs(self):
        from collections import Counter
        report, requests = self.run_fake()
        self.assertEqual('COMPLETED', report['status'])
        self.assertEqual(118, len(requests))
        self.assertEqual({'question': 4, 'query': 12, 'search': 12, 'relevance': 8,
                          'excerpt': 8, 'observation': 8, 'claim': 8, 'claim-type': 8,
                          'pair': 16, 'insight': 4, 'decision-question': 4, 'option': 8,
                          'adopt': 4, 'reject': 4, 'prior-work': 2, 'difference': 8},
                         dict(Counter(r['element_id'].split('.')[0] for r in requests)))
        self.assertTrue(all(r['attempt'] == 1 for r in requests))
        questions = [r for r in requests if r['element_id'].startswith('question.')]
        self.assertEqual(4, len(questions))
        state = load_json(self.engine.path)
        qtexts = [state['answers'][r['element_id']] for r in questions]
        self.assertEqual(4, len(set(qtexts)))
        queries = [state['answers'][r['element_id']] for r in requests if r['element_id'].startswith('query.')]
        self.assertEqual(len(queries), len(set(queries)))
        for request in requests:
            stage = request['element_id'].split('.')[0]
            if stage in {'decision-question', 'option', 'reject'}:
                self.assertEqual(state['proposition'], request['inputs']['proposition'])
            if stage == 'decision-question':
                self.assertIn(request['inputs']['research_question'], qtexts)
            if stage == 'query':
                self.assertEqual(state['config']['search_strategy_descriptions'][request['inputs']['strategy']], request['inputs']['strategy_description'])
            if stage == 'prior-work':
                self.assertEqual(1, len(request['inputs']['quotes']))
            if 'inputs' in request:
                self.assertLessEqual(len(json.dumps(request['inputs'], ensure_ascii=False).encode()), 4096)
        self.assertFalse(report['completion']['pair_review']['truncated'])
        self.assertEqual(len(requests), report['accepted_count'])
        self.assertEqual([], __import__('research_element_validation').check_project(self.project.resolve(), ROOT))

    def test_excerpt_requires_length_and_anchor_or_complete_sentence(self):
        request = self.until_search()
        answer = self.fake(request)
        answer['results'] = answer['results'][:1]
        sentence = 'Puzzling unrelated vocabulary appears here.'
        fragment = 'Detached fragment with no overlap'
        answer['results'][0]['body'] = sentence + ' ' + fragment
        request = self.engine.answer(answer, now=NOW)['next_action']['request']
        request = self.engine.answer(self.fake(request), now=NOW)['next_action']['request']
        self.assertEqual(20, request['answer_format']['min_chars'])
        for value, check in [('e', 'min_chars:20'), (fragment, 'source_terms_or_sentence:question')]:
            report = self.engine.answer(self.reply(request, value), now=NOW)
            retry = report['next_action']['request']
            self.assertEqual(request['element_id'], retry['element_id'])
            self.assertIn(check, [f['check'] for f in retry['previous_failure']])
            request = retry
        report = self.engine.answer(self.reply(request, sentence), now=NOW)
        self.assertNotEqual(request['element_id'], report['next_action']['request']['element_id'])

    def test_copied_observation_and_claim_retry_only_their_element(self):
        self.small_plan()
        report = self.engine.next(now=NOW)
        for stage, input_key in [('observation', 'quote'), ('claim', 'observation')]:
            while not report['next_action']['request']['element_id'].startswith(stage + '.'):
                request = report['next_action']['request']
                report = self.engine.answer(self.fake(request), now=NOW)
            request = report['next_action']['request']
            before = load_json(self.engine.path)['answers']
            self.assertEqual('0.9', request['inputs']['similarity_threshold'])
            report = self.engine.answer(self.reply(request, request['inputs'][input_key]), now=NOW)
            retry = report['next_action']['request']
            self.assertEqual(request['element_id'], retry['element_id'])
            self.assertIn('not_similar_to:' + input_key, [f['check'] for f in retry['previous_failure']])
            self.assertEqual(before, load_json(self.engine.path)['answers'])
            report = self.engine.answer(self.fake(retry), now=NOW)

    def test_pair_candidates_require_same_question_or_shared_evidence(self):
        from research_element_driver import candidate_claim_pairs
        claims = [{'id': 'CL001', 'scope': 'Q001', 'evidence_ids': ['EV001']},
                  {'id': 'CL002', 'scope': 'Q001', 'evidence_ids': ['EV002']},
                  {'id': 'CL003', 'scope': 'Q002', 'evidence_ids': ['EV001']},
                  {'id': 'CL004', 'scope': 'Q002', 'evidence_ids': ['EV003']}]
        pairs = [(a['id'], b['id']) for a, b in candidate_claim_pairs(claims)]
        self.assertEqual([('CL001', 'CL002'), ('CL003', 'CL004'), ('CL001', 'CL003')], pairs)

    def test_pair_cap_is_a_reported_gap_without_extra_inference(self):
        self.engine.config['max_claim_pairs'] = 1
        report, requests = self.run_fake()
        pairs = [r for r in requests if r['element_id'].startswith('pair.')]
        self.assertEqual(1, len(pairs))
        self.assertEqual({'limit': 1, 'selected_count': 1, 'truncated': True}, report['completion']['pair_review'])

    def test_previous_frozen_policy_requires_pinned_code_not_silent_upgrade(self):
        from research_element_validation import require_schema
        config = dict(self.engine.config)
        config['version'] = 1
        with self.assertRaisesRegex(ValueError, 'version: const failed'):
            require_schema(ROOT, 'research-elements-config', config)

    def test_new_entry_defaults_to_elements_and_preview_does_not_write(self):
        from next_action import build_next_action
        root = Path(self.temp.name).resolve()
        before = {p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        preview = build_next_action(root, 'project/elements', 'probe', NOW, protocol_root=ROOT, dry_run=True)
        self.assertEqual('element-request/v1', preview['next_action']['request']['contract_version'])
        self.assertEqual(before, {p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob('*') if p.is_file()})
        live = build_next_action(root, 'project/elements', 'probe', NOW, protocol_root=ROOT)
        self.assertEqual(preview, live)
        self.assertNotIn('write_targets', live)

    def test_legacy_entry_requires_explicit_selection_and_warns(self):
        import shutil
        import warnings
        from task_runtime import initialize_runtime
        from next_action import build_next_action
        from research_routing import LegacyResearchWarning
        root = Path(self.temp.name).resolve()
        for name in ('config', 'schemas'):
            shutil.copytree(ROOT / name, root / name)
        initialize_runtime(root, 'project/elements', initialized_at=NOW, protocol_root=ROOT)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            result = build_next_action(root, 'project/elements', 'probe', NOW, protocol_root=ROOT, research_route='legacy')
        self.assertEqual('TASK001', result['task_id'])
        self.assertTrue(any(issubclass(w.category, LegacyResearchWarning) for w in caught))

    def test_whole_role_supervisor_cannot_start_default_element_project(self):
        from harness_supervisor import Supervisor, SupervisorError
        with self.assertRaisesRegex(SupervisorError, 'HARNESS-RESEARCH-ROUTE'):
            Supervisor(protocol_root=ROOT, work_root=Path(self.temp.name), output_root=Path(self.temp.name) / 'output', project_id='project/elements', run_id='HR122')

    def test_harness_new_request_returns_one_element_without_invoking_worker(self):
        from harness_e2e import run_request
        root = Path(self.temp.name)
        request = yaml.safe_load((ROOT / 'tests/fixtures/harness/request.yaml').read_text())
        request.pop('research_route')
        path = root / 'request.yaml'
        path.write_text(yaml.safe_dump(request))
        def forbidden_worker(*args, **kwargs):
            self.fail('Whole-role worker invoked for new element research')
        report = run_request(protocol_root=ROOT, work_root=root / 'new-work', output_root=root / 'new-output', run_id='HR122', request_path=path, now=NOW, worker_runner=forbidden_worker)
        self.assertEqual('element-request/v1', report['next_action']['request']['contract_version'])
        self.assertEqual('question.Q001', report['next_action']['request']['element_id'])
