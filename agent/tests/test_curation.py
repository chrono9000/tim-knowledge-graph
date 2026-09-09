import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agent.curation import core
from agent.curation.evaluate import assess
from agent.review_groups import plan,save_plan,selected_ids,fingerprint

class CurationTests(unittest.TestCase):
    def setUp(self):
        self.source=json.loads((core.ROOT/'holdout-source.json').read_text())
        self.gold=json.loads((core.ROOT/'holdout-expected.json').read_text())
    def test_materiality_before_numbering(self):
        review=core.compile_review(self.source,self.gold)
        self.assertEqual(12,review['counts']['expandedProposals'])
        self.assertEqual(7,review['counts']['recommendedItems'])
        self.assertEqual(2,review['counts']['duplicateOccurrences'])
        self.assertTrue(all(x['claim']['materiality']['route']=='proposal' for x in review['expanded']))
        self.assertTrue(any(x['supportingEvidence'] for x in review['expanded']))
    def test_scoring_detects_missed_material_claim(self):
        output=copy.deepcopy(self.gold)
        removed=next(c for c in output['claims'] if c['kind']=='commitment')
        output['claims'].remove(removed)
        result=assess(self.gold,output)
        self.assertEqual(1,result['metrics']['claims']['fn'])
        self.assertEqual(1,result['metrics']['commitments']['fn'])
    def test_context_omission_does_not_reduce_material_recall(self):
        output=copy.deepcopy(self.gold)
        output['claims']=[c for c in output['claims'] if c['materiality']['route']=='proposal']
        result=assess(self.gold,output)
        self.assertEqual(1,result['metrics']['claims']['recall'])
        self.assertEqual(0,result['unnecessaryReviewCandidateCount'])
    def test_promoted_context_counts_as_extra_review(self):
        output=copy.deepcopy(self.gold)
        context=next(c for c in output['claims'] if c['materiality']['route']=='evidence')
        context['materiality']['route']='proposal'
        result=assess(self.gold,output)
        self.assertEqual(1,result['metrics']['claims']['fp'])
        self.assertEqual(1,result['unnecessaryReviewCandidateCount'])
    def test_wrong_subject_counts_both_extra_and_missing(self):
        output=copy.deepcopy(self.gold)
        claim=next(c for c in output['claims'] if c['materiality']['route']=='proposal')
        claim['subject']='gauge'
        result=assess(self.gold,output)
        self.assertEqual(1,result['metrics']['subjects']['fp'])
        self.assertEqual(1,result['metrics']['subjects']['fn'])
    def test_no_priority_cap_silences_decisions(self):
        bad=copy.deepcopy(self.gold); decision=next(x for x in bad['claims'] if x['kind']=='decision')
        decision['materiality']['priority']='medium'
        with self.assertRaises(ValueError):core.validate(self.source,bad)
    def test_context_cannot_make_edge(self):
        bad=copy.deepcopy(self.gold);bad['relationships'][0]['evidenceClaim']=next(x['id'] for x in bad['claims'] if x['materiality']['route']=='evidence')
        with self.assertRaises(ValueError):core.validate(self.source,bad)
    def test_explained_gate_and_valid_context_target(self):
        bad=copy.deepcopy(self.gold);bad['claims'][0]['materiality']['reason']='low'
        with self.assertRaises(ValueError):core.validate(self.source,bad)
        bad=copy.deepcopy(self.gold);ctx=next(x for x in bad['claims'] if x['materiality']['route']=='evidence');ctx['supportingFor']=['missing']
        with self.assertRaises(ValueError):core.validate(self.source,bad)

class GroupPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.config=SimpleNamespace(staging_path=self.root/'staging.json',private_graph_path=self.root/'private/master.json')
        self.queue={'batches':[{'id':'b','proposals':[]} ]}
        for i in range(3):
            self.queue['batches'][0]['proposals'].append({'id':str(i),'status':'needs-review','recordType':'node','targetId':'same' if i<2 else 'other',
                'record':{'label':'Item','description':'Ownership' if i<2 else 'Fact','statementType':'assumption'},
                'materiality':{'standalone':True,'priority':'high' if i<2 else 'medium','reason':'Source-supported material claim'},'groupKeys':['project:Example']})
        self.mock=patch('agent.intake.load_staging',side_effect=lambda _:copy.deepcopy(self.queue));self.mock.start()
    def tearDown(self):self.mock.stop();self.temp.cleanup()
    def test_recommended_expanded_and_dedup(self):
        self.assertEqual(1,len(plan(self.config)['groups']['project:Example']))
        self.assertEqual(2,len(plan(self.config,view='expanded')['groups']['project:Example']))
    def test_private_selection_no_mutation(self):
        path=self.root/'private/plan.json';save_plan(self.config,path)
        self.assertEqual(['0','1'],selected_ids(self.config,path,'project:Example'))
        self.assertFalse(self.config.private_graph_path.exists())
    def test_stale_plan_rejected(self):
        path=self.root/'private/plan.json';save_plan(self.config,path)
        self.queue['batches'][0]['proposals'][0]['status']='rejected'
        with self.assertRaises(ValueError):selected_ids(self.config,path,'project:Example')
    def test_rehashed_tampering_rejected(self):
        path=self.root/'private/plan.json';v=save_plan(self.config,path)
        v['groups']['project:Example'][0]['proposalId']='2';v.pop('planHash');v['planHash']=fingerprint(v);path.write_text(json.dumps(v))
        with self.assertRaises(ValueError):selected_ids(self.config,path,'project:Example')
    def test_public_plan_location_rejected(self):
        with self.assertRaises(ValueError):save_plan(self.config,self.root/'public.json')
