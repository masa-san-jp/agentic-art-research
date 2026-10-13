import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from build_handoff import _source_refs, HandoffBuildError
from export_handoff import source_ref_index, HandoffExportError
from work_element_driver import references


def evidence(identifier, sensitivity='PUBLIC_CITABLE'):
    return {'id':identifier,'source_type':'public-web','source_location':'https://example.org/'+identifier,
            'rights_status':'PUBLIC_CITATION_ONLY','sensitivity':sensitivity,'redistribution':'conditional'}


class ProductionReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        project=Path(self.temp.name);(project/'05_production').mkdir()
        self.sources=SimpleNamespace(project=project,protocol_root=ROOT,project_id='project/reference-probe',
            hypotheses=[{'id':'PH001','source_decision_ids':['DC001'],'source_insight_ids':['IN001']}],
            decisions=[{'id':'DC001','evidence_ids':['EV001'],'reason':'finance finding'}],
            insights=[{'id':'IN001','claim_ids':['CL001'],'statement':'finance insight'}],
            claims=[{'id':'CL001','evidence_ids':['EV001']}],evidence=[evidence('EV001'),evidence('EV002')])

    def mapping(self, values):
        (self.sources.project/'05_production/reference-categories.yaml').write_text(yaml.safe_dump({'categories':values}))

    def test_reference_only_art_evidence_survives_handoff_and_index(self):
        self.mapping({'EV001':['CONCEPT'],'EV002':['VISUAL','METHOD']})
        refs=_source_refs(self.sources,{'PH001'})
        self.assertEqual(['EV001','EV002'],refs['evidence_ids'])
        self.assertEqual(['EV001'],self.sources.decisions[0]['evidence_ids'])
        index=source_ref_index(self.sources,{'source_refs':refs})
        by_cat={row['category']:row for row in index['category_access']}
        for category in ['VISUAL','METHOD']:
            self.assertEqual('https://example.org/EV002',by_cat[category]['access_url'])
            self.assertIsNone(by_cat[category]['reason_code'])
        record=next(row for row in index['references'] if row['id']=='EV002')
        self.assertTrue(record['record_hash'].startswith('sha256:'))

    def test_none_does_not_invent_references(self):
        self.mapping({})
        refs=_source_refs(self.sources,{'PH001'});self.assertEqual(['EV001'],refs['evidence_ids'])
        index=source_ref_index(self.sources,{'source_refs':refs})
        method=next(row for row in index['category_access'] if row['category']=='METHOD')
        self.assertIsNone(method['access_url'])

    def test_unknown_ids_categories_duplicates_and_private_are_rejected(self):
        for mapping in [{'EV999':['VISUAL']},{'EV002':['BAD']},{'EV002':['VISUAL','VISUAL']}]:
            self.mapping(mapping)
            with self.assertRaises(HandoffBuildError):_source_refs(self.sources,{'PH001'})
        for sensitivity in ['PRIVATE_RAW','RESTRICTED']:
            self.sources.evidence[1]['sensitivity']=sensitivity;self.mapping({'EV002':['METHOD']})
            with self.assertRaises(HandoffBuildError):_source_refs(self.sources,{'PH001'})
            with self.assertRaises(HandoffExportError):source_ref_index(self.sources,{'source_refs':{'evidence_ids':['EV002']}})

    def test_non_permanent_url_retains_unavailable_reason(self):
        self.sources.evidence[1]['source_location']='https://example.org/art?token=not-permanent'
        self.mapping({'EV002':['VISUAL']});refs=_source_refs(self.sources,{'PH001'})
        index=source_ref_index(self.sources,{'source_refs':refs})
        visual=next(row for row in index['category_access'] if row['category']=='VISUAL')
        self.assertIsNone(visual['access_url']);self.assertEqual('URL_NOT_PERMANENT',visual['reason_code'])

    def run_choices(self, count=2, choose=lambda choices:choices[-2]):
        rows=[{'id':f'EV{i:03}','source_id':f'SRC{i:03}','url':'https://example.org/'+str(i),
               'quote':'An artistic operation shown in a diagram. '*3,'sensitivity':'PUBLIC_CITABLE'} for i in range(1,count+1)]
        rows.append({**rows[-1],'id':'EV999','sensitivity':'PRIVATE_RAW'})
        state={'research':{'evidence':rows,'sources':[{'id':row['source_id'],'title':'Public source'} for row in rows], 'claims':[]},
               'config':{'input_bytes':4096,'reference_categories':[{'id':'VISUAL','label':'visual reference'}]}}
        plan={'title':'Proposed work','decision':{'evidence_ids':['EV001']},'insight':{'claim_ids':[]}}
        records={'points':{'mechanism':'A geometric construction from instructions'},'references':{}}
        seen=[]
        def ask(state,identifier,instruction,inputs,optional=(),*,choices=None,**kwargs):
            self.assertLessEqual(len(json.dumps(inputs,ensure_ascii=False).encode()),state['config']['input_bytes'])
            seen.append((identifier,inputs,choices))
            if False:yield
            return choose(choices)
        with patch('work_element_driver.ask',ask):list(references(state,plan,records))
        return seen,records

    def test_art_outside_plan_support_is_offered_private_is_not(self):
        seen,records=self.run_choices()
        self.assertIn('EV002',seen[0][2]);self.assertNotIn('EV999',seen[0][2])
        self.assertEqual({'EV002':['VISUAL']},records['references'])

    def test_many_candidates_and_finalists_stay_bounded(self):
        seen,records=self.run_choices(600,choose=lambda choices:choices[0])
        ids={source['id'] for _,inputs,_ in seen for source in inputs['sources']}
        self.assertTrue({f'EV{i:03}' for i in range(1,601)}.issubset(ids))
        self.assertTrue(any('.select.' in identifier for identifier,_,_ in seen))
        self.assertEqual('reference.VISUAL',seen[-1][0])
        self.assertTrue(records['references'])

    def test_all_windows_can_be_declined(self):
        seen,records=self.run_choices(30,choose=lambda choices:'NONE')
        self.assertEqual({},records['references'])
        self.assertGreater(len(seen),1)

class ReferencePresentationEdges(unittest.TestCase):
    def test_long_url_is_bounded_without_changing_original_locator(self):
        from work_element_driver import reference_display, fit
        item={'id':'EV001','source_id':'SRC001','url':'https://example.org/'+'x'*5000,'quote':'芸術の操作。'*100}
        state={'config':{'input_bytes':1024}}
        shown=reference_display(state,item,{'SRC001':'見た目と手法'*100})
        self.assertNotIn('url',shown);self.assertEqual('example.org',shown['source_host'])
        self.assertTrue(shown['url_sha256']);self.assertGreater(len(item['url']),5000)
        fitted=fit(state,{'work_title':'A work','sources':[shown]})
        self.assertLessEqual(len(json.dumps(fitted,ensure_ascii=False).encode()),1024)

    def test_legacy_reference_answer_keeps_its_original_id_and_single_request(self):
        rows=[{'id':f'EV{i:03}','source_id':f'SRC{i:03}','url':'https://example.org/'+str(i),'quote':'x'*160} for i in range(1,21)]
        state={'research':{'evidence':rows,'sources':[{'id':row['source_id'],'title':'source'} for row in rows],'claims':[]},
               'config':{'input_bytes':4096,'reference_categories':[{'id':'VISUAL','label':'visual'}]}}
        plan={'title':'work','decision':{'evidence_ids':[row['id'] for row in rows]},'insight':{'claim_ids':[]}}
        records={'points':{'mechanism':'shape'},'references':{}};ids=[]
        def ask(state,identifier,instruction,inputs,optional=(),*,choices=None,**kwargs):
            ids.append(identifier)
            if False:yield
            return 'EV020'
        with patch('work_element_driver.ask',ask):list(references(state,plan,records))
        self.assertEqual(['reference.VISUAL'],ids)
        self.assertEqual({'EV020':['VISUAL']},records['references'])

    def test_minimum_budget_long_unicode_candidates_reduce_to_a_final_choice(self):
        from work_element_driver import fit
        rows=[{'id':f'EV{i:03}','source_id':f'SRC{i:03}','url':'https://example.org/'+str(i)+'x'*5000,
               'quote':'芸術の操作。'*100,'sensitivity':'PUBLIC_CITABLE'} for i in range(1,4)]
        state={'research':{'evidence':rows,'sources':[{'id':row['source_id'],'title':'長い作品名'*100} for row in rows],'claims':[]},
               'config':{'input_bytes':1024,'reference_categories':[{'id':'VISUAL','label':'visual'}]}}
        plan={'title':'Work','decision':{'evidence_ids':['EV001']},'insight':{'claim_ids':[]}}
        records={'points':{'mechanism':'A shape from instructions'},'references':{}};ids=[]
        def ask(state,identifier,instruction,inputs,optional=(),*,choices=None,**kwargs):
            fit(state,inputs,optional)
            ids.append(identifier)
            if False:yield
            return choices[0]
        with patch('work_element_driver.ask',ask):list(references(state,plan,records))
        self.assertEqual('reference.VISUAL',ids[-1])
        self.assertTrue(records['references'])
        self.assertGreater(len(rows[0]['url']),5000)
