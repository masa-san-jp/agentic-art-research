"""Verbatim work projection, boundaries, native scan and Stage B acceptance."""
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
from works_history import build_history, WorksHistoryError
from self_repetition import scan_projects, CONTRACT_VERSION_V2
from research_elements import Engine
import test_research_elements as element_tests

FIXTURE = ROOT / 'tests/fixtures/works-history'
NOW = '2026-10-13T00:00:00Z'


class WorksHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.catalog = self.root / 'catalog'
        shutil.copytree(FIXTURE, self.catalog)
        self.output = self.root / 'history'
        self.work = self.catalog / 'works/W0001-interval'

    def test_verbatim_deterministic_projection_and_source_unchanged(self):
        record = self.work / 'record.md'
        record.write_bytes(record.read_bytes().replace(b'\n', b'\r\n'))
        before = {str(p): p.read_bytes() for p in self.catalog.rglob('*') if p.is_file()}
        receipt = build_history(self.catalog, self.output)
        snapshot = Engine.history_snapshot(None, self.output)
        self.assertEqual(1, receipt['project_count'])
        self.assertEqual(receipt, build_history(self.catalog, self.output))
        second = self.root / 'second'
        build_history(self.catalog, second)
        self.assertEqual(snapshot, Engine.history_snapshot(None, second))
        project = self.output / 'W0001'
        self.assertEqual('creator-synthetic', yaml.safe_load((project / 'manifest.yaml').read_text())['project']['creator_id'])
        self.assertEqual((self.work / 'record.md').read_bytes(), (project / '05_production/creative-direction.md').read_bytes())
        rows = [json.loads(line) for line in (project / '03_knowledge/claims.jsonl').read_text().splitlines()]
        for row in rows:
            relative = row['source_ref'].split('#')[0]
            self.assertIn(row['statement'], (self.catalog / relative).read_text())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.catalog.rglob('*') if p.is_file()})
        self.assertEqual(3, len(snapshot))

    def test_native_scan_same_creator_and_excludes_other_creator(self):
        build_history(self.catalog, self.output)
        candidate = self.root / 'candidate'
        (candidate / '03_knowledge').mkdir(parents=True)
        claim = 'A deferred choice shapes a shared image through a shared interval.'
        (candidate / '03_knowledge/claims.jsonl').write_text(json.dumps({'statement': claim}) + '\n')
        for creator, expected in [('creator-synthetic', 1), ('creator-other', 0)]:
            (candidate / 'manifest.yaml').write_text(yaml.safe_dump({'project': {'id': 'project/new', 'creator_id': creator}}))
            report = scan_projects(candidate, self.output, repository='example/research', source_commit='a'*40,
                                   now=NOW, contract_version=CONTRACT_VERSION_V2)
            self.assertEqual(expected, report['scanned_project_count'])
            if expected:
                self.assertEqual('MEDIUM', report['risk_level'])
                self.assertTrue(report['matches'])

    def test_stage_b_generates_review_and_pins_adapter_history(self):
        # Reuse the existing fake-answer harness without inheriting its test cases.
        helper = element_tests.ResearchElementsTest('test_explicit_history_reuses_native_scan_and_pins_changes')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        manifest = helper.project / 'manifest.yaml'
        doc = yaml.safe_load(manifest.read_text())
        doc['project']['creator_id'] = 'creator-synthetic'
        manifest.write_text(yaml.safe_dump(doc))
        build_history(self.catalog, self.output)
        helper.small_plan()
        helper.engine.next(now=element_tests.NOW, history_root=self.output)
        report, _ = helper.run_fake()
        state = json.loads(helper.engine.path.read_text())
        self.assertEqual(1, state['repetition_scan']['scanned_project_count'])
        self.assertEqual(1, report['completion']['counts']['self_repetition_review'])
        self.assertFalse(any('SELF_REPETITION_REVIEW' in str(item) for item in report['completion']['quality_failures']))
        reviews = yaml.safe_load((helper.project / '04_decisions/self-repetition-review.yaml').read_text())['reviews']
        self.assertEqual(1, len(reviews))
        path = self.output / 'W0001/manifest.yaml'
        path.write_text(path.read_text() + '\n# changed\n')
        with self.assertRaisesRegex(ValueError, 'History inputs changed'):
            helper.engine.next(now=NOW)

    def test_empty_index_does_not_fabricate_work(self):
        (self.catalog / 'works/index.yaml').write_text('version: 1\nrecords: []\n')
        receipt = build_history(self.catalog, self.output)
        self.assertEqual(0, receipt['project_count'])
        self.assertEqual({}, Engine.history_snapshot(None, self.output))

    def test_missing_and_conflicting_creator_rejected_before_publish(self):
        lineage = self.work / 'lineage.json'
        lineage.unlink()
        with self.assertRaisesRegex(WorksHistoryError, 'creator_id'):
            build_history(self.catalog, self.output)
        self.assertFalse(self.output.exists())
        (self.work / 'metadata.yaml').write_text('id: W0001\ncreator_id: creator-one\n')
        lineage.write_text('{"creator_id": "creator-two"}')
        with self.assertRaisesRegex(WorksHistoryError, 'conflicting creator_id'):
            build_history(self.catalog, self.output)

    def test_unsafe_paths_symlinks_and_git_output_rejected(self):
        index = self.catalog / 'works/index.yaml'
        original = index.read_text()
        for path in ('../escape', '/tmp/work', 'works/W0001-interval/../other'):
            doc = yaml.safe_load(original)
            doc['records'][0]['path'] = path
            index.write_text(yaml.safe_dump(doc))
            with self.assertRaisesRegex(WorksHistoryError, 'unsafe work path'):
                build_history(self.catalog, self.output)
        index.write_text(original)
        readme = self.work / 'README.md'
        readme.unlink()
        readme.symlink_to(self.work / 'record.md')
        with self.assertRaisesRegex(WorksHistoryError, 'symlink'):
            build_history(self.catalog, self.output)
        with self.assertRaisesRegex(WorksHistoryError, 'overlap'):
            build_history(self.catalog, self.catalog / 'history')
        (self.root / '.git').mkdir()
        with self.assertRaisesRegex(WorksHistoryError, 'outside Git'):
            build_history(self.catalog, self.output)

    def test_missing_record_duplicate_id_and_malformed_index_rejected(self):
        for path in self.work.glob('*.md'):
            path.unlink()
        with self.assertRaisesRegex(WorksHistoryError, 'requires README.md or record.md'):
            build_history(self.catalog, self.output)
        index = self.catalog / 'works/index.yaml'
        doc = yaml.safe_load(index.read_text())
        doc['records'].append(dict(doc['records'][0]))
        index.write_text(yaml.safe_dump(doc))
        with self.assertRaisesRegex(WorksHistoryError, 'duplicate work id'):
            build_history(self.catalog, self.output)
        index.write_text('version: 1\nrecords: {}\n')
        with self.assertRaisesRegex(WorksHistoryError, 'records list'):
            build_history(self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_sources_refuse_overwrite_and_cli_errors(self):
        build_history(self.catalog, self.output)
        snapshot = Engine.history_snapshot(None, self.output)
        (self.work / 'record.md').write_text('A different recorded statement.\n')
        with self.assertRaisesRegex(WorksHistoryError, 'existing output differs'):
            build_history(self.catalog, self.output)
        self.assertEqual(snapshot, Engine.history_snapshot(None, self.output))
        result = subprocess.run([sys.executable, str(ROOT / 'tools/works_history.py'),
                                 '--works-root', str(self.catalog), '--output', str(self.output)], capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        self.assertIn('WORKS-HISTORY-INVALID', result.stderr)

    def test_multiple_works_are_ordered_and_cli_publishes_to_empty_output(self):
        second = self.catalog / 'works/W0002-other'
        shutil.copytree(self.work, second)
        (second / 'metadata.yaml').write_text('id: W0002\ncreator_id: creator-other\n')
        (second / 'lineage.json').unlink()
        index = self.catalog / 'works/index.yaml'
        doc = yaml.safe_load(index.read_text())
        doc['records'].insert(0, {'id': 'W0002', 'path': 'works/W0002-other'})
        index.write_text(yaml.safe_dump(doc))
        self.output.mkdir()
        result = subprocess.run([sys.executable, str(ROOT / 'tools/works_history.py'),
                                 '--works-root', str(self.catalog), '--output', str(self.output)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(['W0001', 'W0002'], [p['work_id'] for p in receipt['projects']])
        self.assertEqual(2, receipt['project_count'])

    def test_parse_errors_name_the_source_and_heading_only_text_rejected(self):
        lineage = self.work / 'lineage.json'
        lineage.write_text('{broken')
        with self.assertRaisesRegex(WorksHistoryError, 'lineage.json: invalid document'):
            build_history(self.catalog, self.output)
        lineage.write_text('{"creator_id": "creator-synthetic"}')
        for path in self.work.glob('*.md'):
            path.write_text('# Only a heading\n')
        with self.assertRaisesRegex(WorksHistoryError, 'no comparable work text'):
            build_history(self.catalog, self.output)
        index = self.catalog / 'works/index.yaml'
        index.write_text('version: 1\nversion: 1\nrecords: []\n')
        with self.assertRaisesRegex(WorksHistoryError, 'works/index.yaml: invalid document'):
            build_history(self.catalog, self.output)
        self.assertFalse(self.output.exists())


class PlansHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.catalog = self.root / 'catalog'
        shutil.copytree(ROOT / 'tests/fixtures/plans-history', self.catalog)
        self.plan = self.catalog / 'plans/P0001-interval'
        self.output = self.root / 'history'

    def test_plans_only_verbatim_determinism_and_native_scope(self):
        before = {str(p): p.read_bytes() for p in self.catalog.rglob('*') if p.is_file()}
        receipt = build_history(self.catalog, self.output)
        self.assertEqual(receipt, build_history(self.catalog, self.output))
        self.assertEqual(1, receipt['project_count'])
        self.assertEqual('P0001', receipt['projects'][0]['plan_id'])
        project = self.output / 'P0001'
        claims = [json.loads(line) for line in (project / '03_knowledge/claims.jsonl').read_text().splitlines()]
        self.assertEqual('\n\n'.join(dict.fromkeys(claim['statement'] for claim in claims)) + '\n',
                         (project / '05_production/creative-direction.md').read_text())
        self.assertEqual(3, len(claims))
        for claim in claims:
            source = claim['source_ref'].split('#')[0]
            self.assertIn(claim['statement'], (self.catalog / source).read_text())
        self.assertFalse(any('README.md' in ref for ref in receipt['source_hashes']))
        candidate = self.root / 'candidate'
        (candidate / '03_knowledge').mkdir(parents=True)
        (candidate / '03_knowledge/claims.jsonl').write_text(json.dumps({'statement': claims[0]['statement']}) + '\n')
        for creator, expected in [('creator-synthetic', 1), ('creator-other', 0)]:
            (candidate / 'manifest.yaml').write_text(yaml.safe_dump({'project': {'id': 'project/new', 'creator_id': creator}}))
            report = scan_projects(candidate, self.output, repository='example/research', source_commit='a'*40,
                                   now=NOW, contract_version=CONTRACT_VERSION_V2)
            self.assertEqual(expected, report['scanned_project_count'])
            self.assertEqual(bool(expected), bool(report['matches']))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.catalog.rglob('*') if p.is_file()})

    def test_table_template_words_do_not_match_unrelated_candidate(self):
        (self.plan / 'metadata.yaml').write_text('id: P0001\n')
        substantive = '三枚の半透明パネルに光と短い状態ラベルを配置する。'
        (self.plan / 'plan.md').write_text(
            '# Plan\n\nRepeated boilerplate mechanism approval.\n\n'
            '| 項目 | 内容 |\n| --- | --- |\n'
            f'| mechanism | {substantive} |\n'
            '| without_the_technique | CEASES_TO_WORK |\n\n'
            '| 承認ID | 理由 |\n| --- | --- |\n'
            '| AR001 | Repeated boilerplate mechanism approval. |\n\n'
            '| 要件 | 要件内容 | 状態 |\n| --- | --- | --- |\n'
            f'| RQ001 | {substantive} | PLANNED |\n')
        before = (self.plan / 'plan.md').read_bytes()
        build_history(self.catalog, self.output)
        project = self.output / 'P0001'
        rows = [json.loads(line) for line in (project / '03_knowledge/claims.jsonl').read_text().splitlines()]
        self.assertEqual([substantive, substantive], [row['statement'] for row in rows])
        self.assertEqual(substantive + '\n', (project / '05_production/creative-direction.md').read_text())
        self.assertEqual(before, (self.plan / 'plan.md').read_bytes())
        self.assertEqual(build_history(self.catalog, self.output), build_history(self.catalog, self.root / 'second'))
        candidate = self.root / 'candidate'
        (candidate / '03_knowledge').mkdir(parents=True)
        (candidate / '03_knowledge/claims.jsonl').write_text(json.dumps({
            'statement': 'The mechanism inflates a red balloon inside a steel cage.'}) + '\n')
        report = scan_projects(candidate, self.output, repository='example/research', source_commit='a'*40,
                               now=NOW, contract_version=CONTRACT_VERSION_V2)
        self.assertEqual(1, report['scanned_project_count'])
        self.assertEqual([], report['matches'])
        self.assertEqual('LOW', report['risk_level'])

    def test_malformed_plan_table_fails_before_publish(self):
        (self.plan / 'plan.md').write_text('| 項目 | 内容 |\n| --- | --- |\n| mechanism |\n')
        with self.assertRaisesRegex(WorksHistoryError, 'table row width'):
            build_history(self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_stage_b_records_plan_history_review(self):
        helper = element_tests.ResearchElementsTest('test_explicit_history_reuses_native_scan_and_pins_changes')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        manifest = helper.project / 'manifest.yaml'
        doc = yaml.safe_load(manifest.read_text())
        doc['project']['creator_id'] = 'creator-synthetic'
        manifest.write_text(yaml.safe_dump(doc))
        build_history(self.catalog, self.output)
        helper.small_plan()
        helper.engine.next(now=element_tests.NOW, history_root=self.output)
        report, _ = helper.run_fake()
        state = json.loads(helper.engine.path.read_text())
        self.assertEqual(1, state['repetition_scan']['scanned_project_count'])
        self.assertEqual(1, report['completion']['counts']['self_repetition_review'])
        self.assertFalse(any('SELF_REPETITION_REVIEW' in str(item) for item in report['completion']['quality_failures']))

    def test_mixed_catalog_and_empty_works_keep_plans(self):
        shutil.copytree(FIXTURE / 'works', self.catalog / 'works')
        receipt = build_history(self.catalog, self.output)
        self.assertEqual(2, receipt['project_count'])
        (self.catalog / 'works/index.yaml').write_text('version: 1\nrecords: []\n')
        receipt = build_history(self.catalog, self.root / 'empty-works')
        self.assertEqual(1, receipt['project_count'])
        self.assertEqual('P0001', receipt['projects'][0]['plan_id'])

    def test_metadata_only_uses_explicit_content_not_index_or_lineage(self):
        (self.plan / 'plan.md').unlink()
        (self.plan / 'metadata.yaml').write_text('id: P0001\ntitle: An explicit title\n')
        receipt = build_history(self.catalog, self.output)
        self.assertEqual(1, receipt['projects'][0]['claim_count'])
        (self.plan / 'metadata.yaml').write_text('id: P0001\n')
        with self.assertRaisesRegex(WorksHistoryError, 'no comparable plan text'):
            build_history(self.catalog, self.root / 'no-content')

    def test_plan_identity_and_source_boundaries_fail_before_publish(self):
        index = self.catalog / 'plans/index.yaml'
        original = index.read_text()
        for path in ('../escape', 'plans/P0001-interval/../other'):
            doc = yaml.safe_load(original)
            doc['records'][0]['path'] = path
            index.write_text(yaml.safe_dump(doc))
            with self.assertRaisesRegex(WorksHistoryError, 'unsafe plan path'):
                build_history(self.catalog, self.output)
        index.write_text(original)
        (self.plan / 'metadata.yaml').write_text('id: P0002\n')
        with self.assertRaisesRegex(WorksHistoryError, 'plan identity differs'):
            build_history(self.catalog, self.output)
        (self.plan / 'metadata.yaml').write_text('id: P0001\ncreator_id: creator-other\n')
        with self.assertRaisesRegex(WorksHistoryError, 'conflicting creator_id'):
            build_history(self.catalog, self.output)
        (self.plan / 'metadata.yaml').write_text('id: P0001\n')
        (self.plan / 'plan.md').unlink()
        (self.plan / 'plan.md').symlink_to(self.plan / 'README.md')
        with self.assertRaisesRegex(WorksHistoryError, 'symlink'):
            build_history(self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_explicit_owner_scope_keeps_source_identity_and_accepts_missing_identity(self):
        receipt = build_history(self.catalog, self.output, creator_id='creator-owner')
        self.assertEqual('creator-owner', receipt['projects'][0]['creator_id'])
        self.assertEqual('creator-synthetic', receipt['projects'][0]['source_creator_id'])
        (self.plan / 'lineage.json').unlink()
        receipt = build_history(self.catalog, self.root / 'missing-owner', creator_id='creator-owner')
        self.assertIsNone(receipt['projects'][0]['source_creator_id'])
        with self.assertRaisesRegex(WorksHistoryError, 'creator_id'):
            build_history(self.catalog, self.root / 'no-scope')
        with self.assertRaisesRegex(WorksHistoryError, 'explicit creator_id'):
            build_history(self.catalog, self.root / 'invalid-scope', creator_id='unknown')
        (self.plan / 'lineage.json').write_text('{"creator_id": "creator-one"}')
        (self.plan / 'metadata.yaml').write_text('creator_id: creator-two\n')
        with self.assertRaisesRegex(WorksHistoryError, 'conflicting creator_id'):
            build_history(self.catalog, self.root / 'conflict', creator_id='creator-owner')

    def test_unindexed_plan_is_ignored_and_missing_index_rejected(self):
        (self.catalog / 'plans/P0002-unindexed').mkdir()
        receipt = build_history(self.catalog, self.output)
        self.assertEqual(1, receipt['project_count'])
        (self.catalog / 'plans/index.yaml').unlink()
        with self.assertRaisesRegex(WorksHistoryError, 'plans/index.yaml'):
            build_history(self.catalog, self.root / 'missing-index')
