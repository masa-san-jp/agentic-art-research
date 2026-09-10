"""AAK09 acceptance through native project schemas and isolated owner Git history."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import research_memory as memory
import cumulative_specificity as specificity
from run_project import run_offline_fixture
from context_pack import build_context_pack
from complete import evaluate_project

NOW='2026-09-05T00:00:00Z'

class CumulativeSpecificityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Let each runner choose its writable temporary root; macOS and Linux differ.
        cls.code_temp=tempfile.TemporaryDirectory()
        cls.code_root=Path(cls.code_temp.name)/'code'
        shutil.copytree(ROOT,cls.code_root,ignore=shutil.ignore_patterns('.git','.venv','__pycache__'))
        def git(*args):return subprocess.check_output(['git','-C',str(cls.code_root),*args],stderr=subprocess.DEVNULL).decode().strip()
        git('init','-q');git('add','.');git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','Isolated code under test')
        cls.code=git('rev-parse','HEAD')

    @classmethod
    def tearDownClass(cls):cls.code_temp.cleanup()

    def setUp(self):
        # The production path policy still rejects user-controlled symlink aliases.
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'work';self.work.mkdir()
        for name in ('templates','config','schemas'):shutil.copytree(ROOT/name,self.work/name)
        (self.work/'projects').mkdir();(self.work/'data').mkdir()
        run_offline_fixture(self.work,'harmony-study',ROOT/'tests/fixtures/harmony')
        self.project=self.work/'projects/harmony-study'
        manifest=yaml.safe_load((self.project/'manifest.yaml').read_text());manifest['project']['creator_id']='creator-a';(self.project/'manifest.yaml').write_text(yaml.safe_dump(manifest,sort_keys=False))
        self.patch=patch.object(memory,'ROOT',self.code_root);self.patch.start();self.addCleanup(self.patch.stop)
        self.store_root=self.root/'memory';self.store_root.mkdir();gitdir=self.store_root/'objects.git'
        subprocess.run(['git','init','--bare','-q',str(gitdir)],check=True)
        (self.store_root/'store.json').write_text(json.dumps({'owner':memory.OWNER,'creator':'creator-a','collection':'research-a'}))
        def git(*args,body=None):return subprocess.check_output(['git','--git-dir',str(gitdir),'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid',*args],input=body).decode().strip()
        tree=git('mktree',body=b'');initial=git('commit-tree',tree,body=b'Synthetic history');git('update-ref','refs/heads/knowledge',initial)
        self.store=memory.MemoryStore(self.store_root,'creator-a','research-a',self.code)
        self.payload=self.root/'signal.json';self.payload.write_text('{"synthetic_signal":"interrupted interval"}\n')

    def input_record(self,sha):
        return dict(contract_version='artifact-record/v1',record_id='signal-a',revision=1,origin_instance_id='instance-a',creator_id='creator-a',owner_repository='self-model-notes',collection_id='self-a',kind='creative-feedback',payload_schema='synthetic/v1',payload_ref='knowledge/signal-a.json',content_sha256=sha,sources=[dict(locator='https://example.org/synthetic-primary',content_sha256=sha)],derived_from=[],epistemic_status='simulated',lifecycle='accepted',applicability={},rights={'redistribution':False},access_scope='local',consent_ref=None,created_at=NOW,reviewed_at=None,valid_until=None,producer={'kind':'tool','code_commit':self.code},supersedes=[],invalidates=[])

    def request(self):
        hypothesis=json.loads((ROOT/'tests/fixtures/schema-valid/production-hypothesis.json').read_text())
        sha=specificity.hashed(self.payload.read_bytes())
        return dict(contract_version=specificity.REQUEST,rule_version='cumulative-specificity-policy/v1',creator_id='creator-a',origin_instance_id='instance-a',seed=11,at=NOW,project_snapshot=specificity.project_snapshot(self.project),inputs=[dict(id='self-a',record=self.input_record(sha),code_commit=self.code,knowledge_commit='1'*40,payload_path=str(self.payload))],sources=[dict(id='source-a',kind='primary',locator='https://example.org/synthetic-primary',content_sha256=sha,parents=[],input_id='self-a')],candidates=[dict(hypothesis=hypothesis,mode='new-reference',mechanism_ids=['VT001'],input_bindings={'self-a':sha},source_ids=['source-a'],knowledge_refs=[],continuation=None)],memory_query=dict(store_root=str(self.store_root),creator='creator-a',collection='research-a',code_commit=self.code,knowledge_commit=self.store.head(),query='',conditions={},at=NOW))

    def persist_mechanism(self,*,origin='instance-a',creator='creator-a'):
        technique=yaml.safe_load((self.project/'05_production/visual-language.yaml').read_text())['techniques'][0]
        payload=dict(project_id='project/prior-work',source_creator_id=creator,source_snapshot_sha256='1'*64,reuse_trace=[],items=[dict(kind='mechanism',data=technique,rejection_code='none',conditions={},reconsider_when={})])
        record=dict(contract_version='artifact-record/v1',record_id='prior-work',revision=1,origin_instance_id=origin,creator_id='creator-a',owner_repository=memory.OWNER,collection_id='research-a',kind='research-memory',payload_schema=memory.CONTRACT,payload_ref='',content_sha256=memory.hashed(memory.encoded(payload)),sources=[],derived_from=[],epistemic_status='simulated',lifecycle='accepted',applicability={},rights={'knowledge_write':True,'redistribute':False},access_scope='creator-private',consent_ref='synthetic-consent',created_at=NOW,reviewed_at=None,valid_until=None,producer={'kind':'tool','generator_version':memory.CONTRACT,'code_commit':self.code,'run_id':'synthetic'},supersedes=[],invalidates=[])
        record['payload_ref']='knowledge/payloads/'+memory.identity(record)+'.json'
        self.store.commit({'record':record,'payload':payload},self.store.head(),'first','synthetic')
        self.store.index(self.store.head())
        return technique['mechanism']

    def test_derived_handoff_does_not_invalidate_inputs_but_authored_brief_does(self):
        request = self.request()
        before = specificity.evaluate(self.project, request)
        for relative in ("05_production/production-handoff.yaml", "04_decisions/executive-brief.md"):
            (self.project / relative).write_text("generated derivative\n")
        self.assertEqual(request["project_snapshot"], specificity.project_snapshot(self.project))
        self.assertEqual(before["selected_ids"], specificity.evaluate(self.project, request)["selected_ids"])
        brief = self.project / "05_production/production-brief.yaml"
        brief.write_text(brief.read_text() + "\n# changed authored input\n")
        with self.assertRaisesRegex(specificity.SpecificityError, "PROJECT_SNAPSHOT_CHANGED"):
            specificity.evaluate(self.project, request)

    def test_ac1_same_snapshot_rule_seed_reproduces_and_seed_only_breaks_ties(self):
        request=self.request();second=copy.deepcopy(request['candidates'][0]);second['hypothesis']['id']='PH002';second['hypothesis']['title']='Second title';request['candidates'].append(second)
        first=specificity.evaluate(self.project,request);self.assertEqual(first,specificity.evaluate(self.project,request))
        changed=copy.deepcopy(request);changed['seed']=99;other=specificity.evaluate(self.project,changed)
        self.assertEqual(first['candidates'],other['candidates']);self.assertEqual(first['metrics'],other['metrics'])
        self.assertEqual(len(first['selected_ids']),1);self.assertEqual(len(other['selected_ids']),1)
        changed['rule_version']='unknown-rule'
        with self.assertRaisesRegex(specificity.SpecificityError,'RULE_VERSION'):specificity.evaluate(self.project,changed)

    def test_ac2_self_signal_replacement_and_creator_scope_fail(self):
        request=self.request();self.payload.write_text('different personal signal')
        with self.assertRaisesRegex(specificity.SpecificityError,'INPUT_HASH_MISMATCH'):specificity.evaluate(self.project,request)
        request=self.request();request['inputs'][0]['record']['creator_id']='other-creator'
        with self.assertRaisesRegex(specificity.SpecificityError,'SELF_SIGNAL_SCOPE'):specificity.evaluate(self.project,request)
        request=self.request();request['candidates'][0]['input_bindings']['self-a']='a'*64
        self.assertIn('INPUT_BINDING_CHANGED',specificity.evaluate(self.project,request)['candidates'][0]['reasons'])

    def test_ac2_renamed_mechanism_is_detected_and_inherited_attribution_preserved(self):
        self.persist_mechanism(origin='inherited-instance',creator='other-creator')
        request=self.request();request['candidates'][0]['hypothesis']['title']='An entirely new title'
        report=specificity.evaluate(self.project,request);row=report['candidates'][0]
        self.assertIn('STRUCTURAL_REPETITION_REQUIRES_DELTA',row['reasons']);self.assertEqual(row['matches'][0]['classification'],'inherited-work')
        self.assertEqual(report['selected_ids'],[])

    def test_ac2_unsupported_metaphor_and_ai_self_citation_fail(self):
        path=self.project/'05_production/visual-language.yaml';visual=yaml.safe_load(path.read_text());visual['techniques'][0]['reference_ids']=['XR999'];visual['techniques'][0]['mechanism']='The wind remembers everything.';path.write_text(yaml.safe_dump(visual))
        report=specificity.evaluate(self.project,self.request());self.assertTrue(any('GROUNDING' in r for r in report['candidates'][0]['reasons']))
        request=self.request();request['sources'][0]['kind']='ai-derived';request['sources'].append(dict(request['sources'][0],id='requotation',kind='secondary',parents=['source-a'],locator='https://example.org/requotation'));request['candidates'][0]['source_ids']=['requotation'];request['inputs'][0]['record']['sources'].append(dict(locator='https://example.org/requotation',content_sha256=request['sources'][0]['content_sha256']))
        row=specificity.evaluate(self.project,request)['candidates'][0];self.assertEqual(row['independent_primary_roots'],[]);self.assertIn('NO_INDEPENDENT_PRIMARY_ROOT',row['reasons'])
        request['sources'][0]['parents']=['requotation']
        with self.assertRaisesRegex(specificity.SpecificityError,'SOURCE_CYCLE'):specificity.evaluate(self.project,request)

    def test_ac3_explicit_continuation_requires_observed_mechanism_delta(self):
        before=self.persist_mechanism();path=self.project/'05_production/visual-language.yaml';visual=yaml.safe_load(path.read_text());after=before+' A timed shutter reveals the omission twice.';visual['techniques'][0]['mechanism']=after;path.write_text(yaml.safe_dump(visual))
        request=self.request();hit=memory.query_memory(request['memory_query'])['records'][0];key=specificity.reference(hit)
        request['candidates'][0].update(mode='reuse',knowledge_refs=[key],continuation=dict(reference=key,reason='Continue the spatial study with a timed reveal.',before=before,after=after,observable_test='Compare visibility before and after two shutter cycles.'))
        report=specificity.evaluate(self.project,request);self.assertTrue(report['candidates'][0]['continuation_accepted']);self.assertEqual(report['status'],'QUALIFIED')
        request['candidates'][0]['continuation']['after']=before
        self.assertIn('CONTINUATION_DELTA_UNPROVEN',specificity.evaluate(self.project,request)['candidates'][0]['reasons'])

    def test_ac4_empty_history_differs_from_unavailable_and_completion_blocks(self):
        request=self.request();report=specificity.evaluate(self.project,request);self.assertEqual(report['history_status'],'EMPTY_HISTORY')
        request['memory_query']['store_root']=str(self.root/'missing');report=specificity.evaluate(self.project,request)
        self.assertEqual(report['history_status'],'UNAVAILABLE');self.assertEqual(report['status'],'INCOMPLETE')
        (self.project/specificity.REQUEST_PATH).write_text(json.dumps(request))
        pack=build_context_pack(self.work,'project/harmony-study','TASK001','planner')
        self.assertEqual(pack['cumulative_specificity']['history_status'],'UNAVAILABLE')
        completion=evaluate_project(self.work,'project/harmony-study')
        self.assertNotIn(completion['status'],('COMPLETE','COMPLETE_WITH_GAPS'))

    def test_ac5_reuse_comparison_reports_unknown_unscanned_duplicates(self):
        self.persist_mechanism();request=self.request();report=specificity.evaluate(self.project,request)
        self.assertGreater(report['comparison']['with_reuse']['structural_duplicate_matches'],0)
        self.assertIsNone(report['comparison']['without_reuse']['structural_duplicate_matches'])
        self.assertFalse(report['artistic_quality_guarantee'])
        self.assertEqual(report['candidates'][0]['matches'][0]['classification'],'own-work')
        self.assertTrue(report['candidates'][0]['matches'][0]['knowledge_commit'])

    def test_capture_native_mechanism_and_reject_expired_owner_input(self):
        payload=memory.capture(self.work,'project/harmony-study',[{'id':'VT001','rejection_code':'none','conditions':{},'reconsider_when':{}}])
        self.assertEqual(payload['items'][0]['kind'],'mechanism')
        self.assertEqual(payload['items'][0]['data']['id'],'VT001')
        request=self.request();request['inputs'][0]['record']['valid_until']='2026-09-04T00:00:00Z'
        with self.assertRaisesRegex(specificity.SpecificityError,'INPUT_EXPIRED'):specificity.evaluate(self.project,request)
        request=self.request();request['inputs'][0]['record']['lifecycle']='revoked'
        with self.assertRaisesRegex(specificity.SpecificityError,'INPUT_LIFECYCLE'):specificity.evaluate(self.project,request)

    def test_duplicate_source_content_is_one_root_and_project_edit_requires_recheck(self):
        request=self.request();other=dict(request['sources'][0],id='source-b',locator='https://example.org/duplicate')
        request['sources'].append(other);request['inputs'][0]['record']['sources'].append(dict(locator=other['locator'],content_sha256=other['content_sha256']))
        request['candidates'][0]['source_ids'].append('source-b')
        self.assertEqual(len(specificity.evaluate(self.project,request)['candidates'][0]['independent_primary_roots']),1)
        path=self.project/'05_production/visual-language.yaml';path.write_text(path.read_text()+'\n# changed snapshot\n')
        with self.assertRaisesRegex(specificity.SpecificityError,'PROJECT_SNAPSHOT_CHANGED'):specificity.evaluate(self.project,request)

if __name__=='__main__':unittest.main()
