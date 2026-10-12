"""Stage C on the element mechanism: Stage B -> Stage C end to end with the fake answerer."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from _common import load_json
from new_project import create_project
from research_elements import Engine
from work_elements import WorkEngine

NOW = '2026-10-08T00:00:00Z'
spec = importlib.util.spec_from_file_location('fake_research', ROOT / 'tests/fixtures/workers/fake_research_answerer.py')
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)


class WorkElementsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = create_project(Path(self.temp.name), 'elements', 'Elements', protocol_root=ROOT, created_at=NOW)
        (self.project / '00_intake/creative-intent.md').write_text('How does a deferred choice shape a shared image?')
        path = self.project / '01_planning/research-plan.yaml'
        plan = yaml.safe_load(path.read_text())
        plan['element_research'] = {'question_count': 1, 'search_limit': 2}
        plan['minimums'] = {'evidence': 2, 'claims': 2, 'insights': 1, 'decisions': 1,
                            'requirements': 4, 'reason': 'Synthetic bounded exploratory qualification.'}
        path.write_text(yaml.safe_dump(plan, sort_keys=False))

    def run_engine(self, engine, mutate=None, expect_blocked=False):
        report = engine.next(now=NOW)
        requests = []
        while report['next_action']:
            request = report['next_action']['request']
            requests.append(request)
            answer = fake.answer(request)
            if mutate:
                mutate(request, answer)
            report = engine.answer(answer, now=NOW)
            if not expect_blocked:
                self.assertNotEqual('BLOCKED', report['status'], report)
        return report, requests

    def stage_b(self):
        engine = Engine(self.project)
        # One insight per claim gives two decisions, so two work plans can be compared.
        engine.config['insight_group_size'] = 1
        def distinct(request, answer):
            # Decisions that adopt the same option would give the same plan; adopt another one.
            if request['element_id'] == 'adopt.DC002':
                answer['value'] = request['answer_format']['choices'][-1]
            if request['element_id'] == 'difference.EV002.DC002':
                answer['value'] = 'The fixed image in the source differs because the final composition here locks at opening.'
        report, _ = self.run_engine(engine, distinct)
        self.assertEqual('COMPLETED', report['status'])

    def test_stage_c_requires_completed_stage_b(self):
        with self.assertRaisesRegex(ValueError, 'Stage B run'):
            WorkEngine(self.project).next(now=NOW)
        Engine(self.project).next(now=NOW)
        with self.assertRaisesRegex(ValueError, 'COMPLETED Stage B'):
            WorkEngine(self.project).next(now=NOW)

    def test_stage_b_to_stage_c_end_to_end_by_elements(self):
        from research_element_validation import check_project
        self.stage_b()
        engine = WorkEngine(self.project)
        report, requests = self.run_engine(engine)
        self.assertEqual('COMPLETED', report['status'])
        self.assertEqual('C', report['completion']['stage'])
        self.assertEqual(4, report['completion']['counts']['requirements'])
        self.assertNotIn('QUALITY-MINIMUM-REQUIREMENTS', [f['id'] for f in report['completion']['quality_failures']])
        # One value per request, inside the byte budget, never asking for files.
        for request in requests:
            self.assertEqual('element-request/v1', request['contract_version'])
            self.assertLessEqual(len(json.dumps(request['inputs'], ensure_ascii=False).encode()), 4096)
            self.assertIn('forbidden:∩,無し,未記入,未確定', request['checks'])
        ids = [r['element_id'] for r in requests]
        self.assertTrue(any(i.startswith('plan.PH002.') for i in ids))
        # Merely plainer re-asks the mechanism alone; the plan and earlier key points are kept.
        self.assertEqual(['point.PH001.mechanism.1', 'point.PH001.without-technique.1',
                          'point.PH001.mechanism.2', 'point.PH001.without-technique.2'],
                         [i for i in ids if '.mechanism.' in i or '.without-technique.' in i])
        retry = next(r for r in requests if r['element_id'] == 'point.PH001.mechanism.2')
        self.assertIn('previous_mechanism', retry['inputs'])
        self.assertEqual(1, ids.count('point.PH001.claim'))

        def load(relative):
            return yaml.safe_load((self.project / relative).read_text())
        brief = load('05_production/production-brief.yaml')
        self.assertEqual('CEASES_TO_WORK', brief['concept']['without_the_technique'])
        self.assertEqual(3, len(brief['completion_image']['encounter']))
        self.assertIn('logbook dates it', brief['concept']['mechanism'])
        self.assertTrue(brief['concept']['precedents'])
        self.assertTrue(brief['research_summary']['what_was_read'].startswith('取得資料'))
        for relative in ('05_production/production-brief.yaml', '05_production/creative-direction.md',
                         '04_decisions/production-hypotheses.yaml', '05_production/prototype-plans.yaml'):
            text = (self.project / relative).read_text()
            self.assertNotIn('未記入', text)
            self.assertNotIn('未確定', text)
        hypotheses = load('04_decisions/production-hypotheses.yaml')['hypotheses']
        self.assertEqual(['RECOMMENDED', 'ALTERNATIVE'], [h['recommendation'] for h in hypotheses])
        self.assertEqual(['DC001'], hypotheses[0]['source_decision_ids'])
        self.assertNotEqual(hypotheses[0]['source_decision_ids'], hypotheses[1]['source_decision_ids'])
        comparison = load('04_decisions/hypothesis-comparison.yaml')['comparisons'][0]
        self.assertEqual('PH001', comparison['recommended_hypothesis_id'])
        tests = load('05_production/acceptance-tests.yaml')['acceptance_tests']
        requirements = load('05_production/production-requirements.yaml')['requirements']
        self.assertEqual([t['target_requirement'] for t in tests], [r['id'] for r in requirements])
        self.assertEqual({'mandatory'}, {r['priority'] for r in requirements})
        prototype = load('05_production/prototype-plans.yaml')['prototype_plans'][0]
        self.assertEqual(['PT001', 'PT002', 'PT003'], [t['id'] for t in prototype['tasks']])
        self.assertEqual(['PT002'], prototype['tasks'][2]['depends_on'])
        self.assertIn('作品の核', (self.project / '05_production/creative-direction.md').read_text())
        self.assertEqual([], check_project(self.project.resolve(), ROOT))
        # The materialized project passes the repository gates, including requirement tests.
        from validate import validate_repository
        root = Path(self.temp.name).resolve()
        for name in ('config', 'schemas'):
            shutil.copytree(ROOT / name, root / name)
        (root / 'data').mkdir()
        findings = validate_repository(root, 'project/elements', protocol_root=ROOT)
        self.assertEqual([], [(f.rule, f.message) for f in findings])
        command = [sys.executable, str(ROOT / 'tools/validate.py'), '--check', '--protocol-root', str(ROOT),
                   '--work-root', str(root), '--project', 'project/elements']
        self.assertEqual(0, subprocess.run(command, capture_output=True, text=True).returncode)
        path = self.project / '05_production/production-requirements.yaml'
        path.write_text(path.read_text().replace('gallery light', 'daylight'))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertIn('WORK-ELEMENT-PROJECTION', result.stdout)

    def test_retry_only_failed_element_and_placeholder_rejected(self):
        self.stage_b()
        engine = WorkEngine(self.project)
        request = engine.next(now=NOW)['next_action']['request']
        bad = fake.answer(request)
        bad['value'] = '未記入'
        report = engine.answer(bad, now=NOW)
        retry = report['next_action']['request']
        self.assertEqual((request['element_id'], 2), (retry['element_id'], retry['attempt']))
        self.assertTrue(any(f['check'].startswith('forbidden:') for f in retry['previous_failure']))
        answer = fake.answer(retry)
        report = engine.answer(answer, now=NOW)
        self.assertEqual(report, engine.answer(answer, now=NOW))  # replay has no second effect
        self.assertEqual(1, report['accepted_count'])

    def test_second_plan_and_optional_item_are_skipped_not_blocking(self):
        self.stage_b()
        def fail(request, answer):
            if request['element_id'] in ('plan.PH001.exclude', 'plan.PH002.title'):
                answer['value'] = ''
        report, _ = self.run_engine(WorkEngine(self.project), fail)
        self.assertEqual('COMPLETED', report['status'])
        self.assertEqual(['plan.PH001.exclude', 'plan.PH002.title'], report['abandoned'])
        self.assertEqual(['PH002'], report['completion']['dropped_plans'])
        hypotheses = yaml.safe_load((self.project / '04_decisions/production-hypotheses.yaml').read_text())['hypotheses']
        self.assertEqual(1, len(hypotheses))
        self.assertEqual([], hypotheses[0]['excludes'])
        self.assertIn('single_hypothesis_rationale', hypotheses[0])
        self.assertEqual([], yaml.safe_load((self.project / '04_decisions/hypothesis-comparison.yaml').read_text())['comparisons'])

    def test_required_element_blocks_with_named_failure(self):
        self.stage_b()
        def fail(request, answer):
            if request['element_id'] == 'point.PH001.claim':
                answer['value'] = 'No.'
        report, _ = self.run_engine(WorkEngine(self.project), fail, expect_blocked=True)
        self.assertEqual('BLOCKED', report['status'])
        self.assertEqual('point.PH001.claim', report['blocked']['element_id'])
        # Nothing is assembled from a blocked run; the templates stay untouched.
        self.assertIn('未記入', (self.project / '05_production/production-brief.yaml').read_text())

    def test_mechanism_stays_plainer_after_last_round_without_discarding_the_plan(self):
        self.stage_b()
        def plainer(request, answer):
            if '.without-technique.' in request['element_id']:
                answer['value'] = {'answer': False, 'reason': 'Without the ink it is still a drawing wall.'}
            if '.mechanism.' in request['element_id']:
                number = request['element_id'].rsplit('.', 1)[1]
                answer['value'] = [
                    'Visitors stamp over the grid and the ink fades slowly, so each layer stays faintly visible.',
                    'Each day uses a new ink color and the logbook dates it, so revisions can be ordered by eye.',
                    'A camera photographs the wall hourly and prints the sequence as a strip beside the wall.'][int(number) - 1]
        report, requests = self.run_engine(WorkEngine(self.project), plainer)
        self.assertEqual('COMPLETED', report['status'])
        self.assertEqual('MERELY_PLAINER', report['completion']['without_the_technique'])
        self.assertIn('WORK-MECHANISM-MERELY-PLAINER', [f['id'] for f in report['completion']['quality_failures']])
        self.assertEqual('INCOMPLETE', report['completion']['status'])
        last = [r for r in requests if r['element_id'] == 'point.PH001.mechanism.3'][0]
        self.assertEqual(2, len(last['inputs']['earlier_mechanisms']))
        self.assertEqual(3, sum(r['element_id'].startswith('point.PH001.mechanism.') for r in requests))
        brief = yaml.safe_load((self.project / '05_production/production-brief.yaml').read_text())
        self.assertIn('camera', brief['concept']['mechanism'])
        self.assertEqual('MERELY_PLAINER', brief['concept']['without_the_technique'])

    def test_changed_stage_b_and_foreign_output_fail_closed(self):
        self.stage_b()
        engine = WorkEngine(self.project)
        self.run_engine(engine)
        path = self.project / '05_production/production-brief.yaml'
        original = path.read_text()
        path.write_text(original + '\n# edited\n')
        with self.assertRaisesRegex(ValueError, 'output changed'):
            engine.next(now=NOW)
        path.write_text(original)
        state_path = self.project / '07_runtime/research-elements.json'
        state = load_json(state_path)
        state['completion']['status'] = 'COMPLETE'
        state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'Stage B checkpoint changed'):
            engine.next(now=NOW)

    def test_driver_entry_keeps_stage_c_running_across_stage_b_clock(self):
        from next_action import build_next_action
        from work_elements import next_element
        self.stage_b()
        first = next_element(self.project, now='2026-10-08T00:01:00Z')
        self.assertEqual('C', first['stage'])
        request = first['next_action']['request']
        # Asking Stage B again advances its clock; Stage C must still accept answers.
        Engine(self.project).next(now='2026-10-08T00:05:00Z')
        report = WorkEngine(self.project).answer(fake.answer(request), now='2026-10-08T00:06:00Z')
        self.assertEqual(1, report['accepted_count'])
        again = next_element(self.project, now='2026-10-08T00:07:00Z')
        self.assertEqual(report['next_action'], again['next_action'])
        self.assertTrue(callable(build_next_action))

    def test_placeholder_and_nobody_answers_are_retried(self):
        self.stage_b()
        def empty(request, answer):
            if request['element_id'] == 'plan.PH001.exclude':
                answer['value'] = 'なし'
            if request['element_id'] == 'point.PH001.who-disagrees' and request['attempt'] == 1:
                answer['value'] = '誰もいない'
        report, requests = self.run_engine(WorkEngine(self.project), empty)
        self.assertEqual('COMPLETED', report['status'])
        self.assertIn('plan.PH001.exclude', report['abandoned'])
        retried = [r for r in requests if r['element_id'] == 'point.PH001.who-disagrees']
        self.assertEqual(2, len(retried))
        self.assertTrue(retried[1]['previous_failure'][0]['check'].startswith('not_one_of:'))
        brief = yaml.safe_load((self.project / '05_production/production-brief.yaml').read_text())
        self.assertEqual('Collectors who need a single final object to buy', brief['message']['who_disagrees'])

    def test_existing_work_content_is_not_overwritten(self):
        self.stage_b()
        (self.project / '05_production/creative-direction.md').write_text('# Hand\n\n## 作品の核\n\nhand written\n')
        with self.assertRaisesRegex(ValueError, 'existing work content'):
            WorkEngine(self.project).next(now=NOW)

    def test_inputs_fit_the_byte_budget_without_losing_checked_material(self):
        from work_element_driver import fit
        state = {'config': {'input_bytes': 600}}
        inputs = fit(state, {'insight': 'あ' * 150, 'claims': ['い' * 100, 'う' * 100], 'proposition': 'え' * 120},
                     ('claims',))
        self.assertLessEqual(len(json.dumps(inputs, ensure_ascii=False).encode()), 600)
        self.assertIn('insight', inputs)


if __name__ == '__main__':
    unittest.main()


class StageDStartTest(unittest.TestCase):
    """Issue #134: when the production plan may start, and the handoff it then receives."""
    setUp = WorkElementsTest.setUp
    run_engine = WorkElementsTest.run_engine
    stage_b = WorkElementsTest.stage_b

    def plainer(self, request, answer):
        if '.without-technique.' in request['element_id']:
            answer['value'] = {'answer': False, 'reason': 'Without the ink it is still a drawing wall.'}
        if '.mechanism.' in request['element_id']:
            number = request['element_id'].rsplit('.', 1)[1]
            answer['value'] = [
                'Visitors stamp over the grid and the ink fades slowly, so each layer stays faintly visible.',
                'Each day uses a new ink color and the logbook dates it, so revisions can be ordered by eye.',
                'A camera photographs the wall hourly and prints the sequence as a strip beside the wall.'][int(number) - 1]

    def test_stage_c_to_handoff_and_export_by_elements(self):
        from build_handoff import build_handoff
        from export_handoff import export_handoff
        from research_element_validation import check_project
        from work_elements import stage_d_start
        self.stage_b()
        self.assertEqual(['STAGE_C_NOT_STARTED'], stage_d_start(self.project)['reasons'])
        report, requests = self.run_engine(WorkEngine(self.project))
        ids = [r['element_id'] for r in requests]
        self.assertEqual(['reference.CONCEPT', 'reference.VISUAL', 'reference.METHOD'], [i for i in ids if i.startswith('reference.')])
        reference = next(r for r in requests if r['element_id'] == 'reference.VISUAL')
        self.assertEqual('choice', reference['answer_format']['type'])
        self.assertEqual('NONE', reference['answer_format']['choices'][-1])
        categories = yaml.safe_load((self.project / '05_production/reference-categories.yaml').read_text())['categories']
        self.assertEqual({'EV001': ['CONCEPT', 'VISUAL', 'METHOD']}, categories)
        plans = yaml.safe_load((self.project / '05_production/prototype-plans.yaml').read_text())['prototype_plans']
        self.assertEqual(['PP001', 'PP002'], [p['id'] for p in plans])
        self.assertEqual('digital-prototype-renderer', plans[1]['executor_capability'])
        self.assertEqual({'READ_ONLY', 'REPOSITORY_WRITE'}, {t['effect_type'] for t in plans[1]['tasks']})
        direction = (self.project / '05_production/creative-direction.md').read_text()
        self.assertIn('## 完了経路\n\n1. Print the grid', direction)

        start = stage_d_start(self.project)
        self.assertTrue(start['allowed'], start['reasons'])
        gaps = start['completion_report']['gaps']
        self.assertIn('QUALITY-REQUIRED-SELF_REPETITION_REVIEW', [g['id'] for g in gaps])
        self.assertIn('GAP-RIGHTS_REVIEW_COMPLETE', [g['id'] for g in gaps])
        self.assertTrue(all(g['blocking'] is False and g['resolution_owner'] == 'research' for g in gaps))
        self.assertEqual('INCOMPLETE', start['completion_report']['status'])
        manifest = (self.project / 'manifest.yaml').read_text()

        root = Path(self.temp.name).resolve()
        path = build_handoff(root, 'project/elements', protocol_root=ROOT, generated_at=NOW, research_commit='a' * 40)
        handoff = yaml.safe_load(path.read_text())
        self.assertEqual('READY', handoff['status'])
        self.assertEqual('PH001', handoff['selection']['selected_hypothesis_id'])
        self.assertEqual(['PP001', 'PP002'], handoff['prototype_plan_ids'])
        self.assertEqual(len(gaps), len(handoff['open_gaps']))
        self.assertFalse(any(g['blocking'] for g in handoff['open_gaps']))
        self.assertEqual(manifest, (self.project / 'manifest.yaml').read_text())  # Stage B's file is untouched
        bundle = export_handoff(root, 'project/elements', root / 'bundle', allow_dirty=True, protocol_root=ROOT)
        index = yaml.safe_load((bundle / 'artifacts/source-ref-index.yaml').read_text())
        access = {row['category']: row for row in index['category_access']}
        for category in ('CONCEPT', 'VISUAL', 'METHOD'):
            self.assertEqual('https://example.invalid/choice', access[category]['access_url'])
        self.assertEqual([], check_project(self.project.resolve(), ROOT))

    def test_stage_d_waits_for_a_mechanism_that_ceases_to_work(self):
        from build_handoff import HandoffBuildError, build_handoff
        from handoff_common import HandoffInputError
        from work_elements import stage_d_start
        self.stage_b()
        report, _ = self.run_engine(WorkEngine(self.project), self.plainer)
        self.assertEqual('COMPLETED', report['status'])
        start = stage_d_start(self.project)
        self.assertFalse(start['allowed'])
        self.assertTrue(any(r.startswith('MECHANISM_MERELY_PLAINER') for r in start['reasons']), start['reasons'])
        with self.assertRaisesRegex((HandoffInputError, HandoffBuildError), 'STAGE_D_NOT_READY: MECHANISM_MERELY_PLAINER'):
            build_handoff(Path(self.temp.name).resolve(), 'project/elements', protocol_root=ROOT,
                          generated_at=NOW, research_commit='a' * 40)
        command = [sys.executable, str(ROOT / 'tools/work_elements.py'), 'stage-d-start', '--project', str(self.project)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertFalse(json.loads(result.stdout)['allowed'])

    def test_stage_d_waits_for_the_requirement_minimum(self):
        from work_elements import stage_d_start
        path = self.project / '01_planning/research-plan.yaml'
        plan = yaml.safe_load(path.read_text())
        plan['minimums']['requirements'] = 5
        path.write_text(yaml.safe_dump(plan, sort_keys=False))
        self.stage_b()
        self.run_engine(WorkEngine(self.project))
        self.assertIn('REQUIREMENTS_BELOW_MINIMUM: 4/5', stage_d_start(self.project)['reasons'])
