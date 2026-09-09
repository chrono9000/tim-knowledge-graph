import copy,json,unittest
from agent.trial_v3 import contract as c
from agent.chatgpt_export import validate_export

class RealismTests(unittest.TestCase):
    def setUp(self):
        self.source=json.loads((c.ROOT/'synthetic-export.json').read_text(encoding='utf-8'))
        self.gold=json.loads((c.ROOT/'expected.json').read_text(encoding='utf-8'))
    def test_actual_export_reader_accepts(self):
        result=validate_export(c.ROOT/'synthetic-export.json')
        self.assertEqual(6,len(result.conversations));self.assertFalse(result.failures)
        self.assertEqual(222,len(c.messages(self.source)))
    def test_gold_schema_and_evidence(self): c.validate(self.source,self.gold)
    def test_no_supplied_entities_or_speaker_map(self):
        text=json.dumps(self.source)
        self.assertNotIn('speaker_map',text);self.assertNotIn('"entities"',text)
        for convo in self.source:
            for node in convo['mapping'].values():
                if node['message']: self.assertIsNone(node['message']['author']['name'])
    def test_offer_requires_recommendation(self):
        bad=copy.deepcopy(self.gold)
        offer=next(x for x in bad['claims'] if x['quote'].startswith('I can help'))
        offer['kind']='statement';offer['recommendationState']='not-applicable'
        with self.assertRaises(ValueError): c.validate(self.source,bad)
    def test_duplicate_cannot_cross_entities(self):
        bad=copy.deepcopy(self.gold); bad['claims'][-1]['duplicateOf']='c1'
        with self.assertRaises(ValueError): c.validate(self.source,bad)
    def test_nonexistent_alias_rejected(self):
        bad=copy.deepcopy(self.gold);bad['entities'][0]['aliases'].append('An invented identity')
        with self.assertRaises(ValueError):c.validate(self.source,bad)
