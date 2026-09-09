import copy,unittest
from agent.trial_v2.evaluate import score

class MetricTests(unittest.TestCase):
    def setUp(self):
        self.claim={'id':'c1','messageId':'m1','quote':'An explicit source assertion.', 'kind':'statement','speaker':'p1','subject':'s1','decisionOwner':None,'commitment':None,'amount':None,'support':[]}
        self.gold={'claims':[self.claim],'relationships':[],'flags':[]}
    def test_missing_claim_penalizes_recall(self):
        m=score(self.gold,{'claims':[],'relationships':[],'flags':[]})['metrics']
        self.assertEqual(1,m['claims']['fn']); self.assertEqual(0,m['claims']['recall'])
    def test_classification_error_is_fp_and_fn(self):
        actual=copy.deepcopy(self.gold); actual['claims'][0]['kind']='recommendation'
        m=score(self.gold,actual)['metrics']
        self.assertEqual(1,m['claims']['tp']); self.assertEqual(1,m['classifications']['fp']); self.assertEqual(1,m['classifications']['fn'])
    def test_wrong_speaker_penalizes_both(self):
        actual=copy.deepcopy(self.gold); actual['claims'][0]['speaker']='p2'
        m=score(self.gold,actual)['metrics']['speakers']
        self.assertEqual((0,1,1),(m['tp'],m['fp'],m['fn']))
    def test_extra_flag_adds_one_unnecessary_review(self):
        actual=copy.deepcopy(self.gold)
        actual['flags']=[{'code':'ambiguous-reference','field':'subject','claimIds':['c1'],'reason':'Example test flag only.'}]
        result=score(self.gold,actual)
        self.assertEqual(1,result['unnecessaryReviewCandidateCount'])
        self.assertEqual(1,result['metrics']['uncertaintyFlags']['fp'])
