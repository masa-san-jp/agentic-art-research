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
