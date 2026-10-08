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
