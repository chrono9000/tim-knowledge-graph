import copy,json,unittest
from agent.trial_v2 import contract as c

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.cases=json.loads((c.ROOT/'holdout-inputs.json').read_text())
        self.gold=json.loads((c.ROOT/'holdout-expected.json').read_text())
    def test_gold_valid(self):
        for case,g in zip(self.cases,self.gold): c.validate(case,g)
    def test_speaker_cannot_be_topic(self):
        g=copy.deepcopy(self.gold[0]); g['claims'][0]['speaker']='a-vials'
        with self.assertRaises(ValueError): c.validate(self.cases[0],g)
    def test_ownerless_decision_rejected(self):
        g=copy.deepcopy(self.gold[2]); g['claims'][0]['kind']='decision'
        with self.assertRaises(ValueError): c.validate(self.cases[2],g)
    def test_mentioned_person_cannot_be_decision_owner(self):
        g=copy.deepcopy(self.gold[11]); g['claims'][0]['decisionOwner']='p-vik'
        with self.assertRaises(ValueError): c.validate(self.cases[11],g)
    def test_claim_target_rejected(self):
        g=copy.deepcopy(self.gold[4]); g['relationships'][0]['target']='c1'
        with self.assertRaises(ValueError): c.validate(self.cases[4],g)
    def test_wrong_relationship_type_rejected(self):
        g=copy.deepcopy(self.gold[4]); g['relationships'][0]['type']='conflicts-with'
        with self.assertRaises(ValueError): c.validate(self.cases[4],g)
    def test_duplicate_edge_rejected(self):
        g=copy.deepcopy(self.gold[4]); g['relationships'].append(g['relationships'][0])
        with self.assertRaises(ValueError): c.validate(self.cases[4],g)
    def test_different_speaker_conflict_rejected(self):
        g=copy.deepcopy(self.gold[5]); g['flags'][0]['claimIds']=['c2','c4']
        with self.assertRaises(ValueError): c.validate(self.cases[5],g)
    def test_flag_must_explain_field(self):
        g=copy.deepcopy(self.gold[2]); g['flags'][0]['reason']='unclear'
        with self.assertRaises(ValueError): c.validate(self.cases[2],g)
    def test_date_flag_must_match(self):
        g=copy.deepcopy(self.gold[7]); g['flags']=[]
        with self.assertRaises(ValueError): c.validate(self.cases[7],g)
    def test_ambiguous_date_cannot_be_definite(self):
        g=copy.deepcopy(self.gold[7]); g['claims'][0]['commitment']['due']['date']='2027-04-05'
        with self.assertRaises(ValueError): c.validate(self.cases[7],g)
    def test_assistant_cannot_adopt(self):
        g=copy.deepcopy(self.gold[1]); g['claims'][0]['kind']='decision'; g['claims'][0]['decisionOwner']='p-ada'
        with self.assertRaises(ValueError): c.validate(self.cases[1],g)
