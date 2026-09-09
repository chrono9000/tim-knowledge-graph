import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent.trial import evaluate as t


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.case = t.load_inputs()[0]
        self.row = dict(message_id='m1', quote='Maya owns Project Meadow.', category='project',
                        epistemic='user-statement', confidence=.5, owner='Maya', exact_wording=False,
                        attributes={k: [] if k == 'deadlineCandidates' else None for k in t.ATTRIBUTES},
                        uncertainties=[], support=[{'message_id': 'm1', 'quote': 'Maya owns Project Meadow.'}])
        self.response = {'claims': [self.row], 'relationships': []}

    def test_synthetic_split_and_projection(self):
        cases = t.load_inputs()
        self.assertEqual(8, sum(c['split'] == 'heldout' for c in cases))
        for case in cases:
            payload = json.loads(t.request_for(case)['input'])
            self.assertEqual({'context', 'messages'}, set(payload))
            self.assertTrue(case['synthetic'])

    def test_pending_cannot_execute(self):
        with self.assertRaises(RuntimeError):
            t.PendingModelAdapter().extract(t.messages(self.case))

    def test_budget_caps_calls_and_storage(self):
        budget = t.TrialBudget()
        request = t.request_for(self.case)
        for _ in range(16):
            budget.reserve(request)
        self.assertEqual('0.1792', str(budget.reserved_usd))
        with self.assertRaises(ValueError):
            budget.reserve(request)
        request['store'] = True
        with self.assertRaises(ValueError):
            t.TrialBudget().reserve(request)

    def test_invalid_evidence_and_types_rejected(self):
        for key, value in [('quote', 'Invented fact'), ('confidence', float('nan')), ('exact_wording', 'yes')]:
            response = copy.deepcopy(self.response)
            response['claims'][0][key] = value
            with self.assertRaises(ValueError):
                t.RecordedTrialAdapter(response).extract(t.messages(self.case))
        self.row['attributes']['amount'] = '2500'
        with self.assertRaises(ValueError):
            t.RecordedTrialAdapter(self.response).extract(t.messages(self.case))

    def test_missing_fields_rejected(self):
        del self.row['support']
        with self.assertRaises(ValueError):
            t.RecordedTrialAdapter(self.response).extract(t.messages(self.case))

    def test_assistant_decision_rejected(self):
        msg = t.Message('m1', 'assistant', self.row['quote'], None)
        self.row['epistemic'] = 'decision'
        with self.assertRaises(ValueError):
            t.RecordedTrialAdapter(self.response).extract((msg,))

    def test_adoption_requires_earlier_cited_assistant(self):
        self.row['attributes']['adoptedRecommendation'] = 'missing'
        with self.assertRaises(ValueError):
            t.RecordedTrialAdapter(self.response).extract(t.messages(self.case))

    def test_review_stays_private_unapproved(self):
        review = t.review_recorded(self.case, self.response, origin='test-double')
        self.assertFalse(review['aiQualityEvidence'])
        for row in review['proposals']:
            self.assertFalse(row['approvalEligible'])
            self.assertEqual('private', row['classification'])
            self.assertEqual('needs-review', row['status'])
            self.assertEqual(22, len(row['evaluatedRuleIds']))

    def test_redundant_relationships(self):
        claims = t.RecordedTrialAdapter(self.response).extract(t.messages(self.case))
        direct = {'source': 'Maya', 'relationship': 'owner-of', 'target': 'Project Meadow'}
        redundant = {**direct, 'target': self.row['quote']}
        clean, removed = t.normalize_relationships([direct, direct, redundant], claims)
        self.assertEqual([['Maya', 'owner-of', 'Project Meadow']], clean)
        self.assertEqual(2, removed)

    def test_metrics_detect_invention_and_uncertainty(self):
        expected = {'claims': [{**self.row, 'attributes': {'subject': 'Maya'}, 'uncertainties': ['scope-unclear']}], 'relationships': []}
        actual = {'claims': [{**self.row, 'attributes': {'subject': 'Nora'}, 'uncertainties': ['scope-unclear', 'date-ambiguous']}],
                  'relationships': [['Nora', 'owner-of', 'Project Meadow']]}
        m = t.score(expected, actual)['metrics']
        self.assertEqual(1, m['unsupportedClaimCandidates'])
        self.assertEqual(1, m['incorrectRelationships'])
        self.assertEqual(1, m['correctUncertaintyFlags'])
        self.assertEqual(1, m['spuriousUncertaintyFlags'])

    def test_preparation_is_offline_and_separate(self):
        root = t.ROOT.parents[1]
        protected = [root/'data/graph.json', root/'data/private', root/'data/staging']
        def snapshot():
            files = []
            for p in protected:
                files.extend(p.rglob('*') if p.is_dir() else [p])
            return {str(p): p.read_bytes() for p in files if p.is_file()}
        before = snapshot()
        with tempfile.TemporaryDirectory() as output:
            report = t.prepare(Path(output))
            self.assertFalse(report['externalModelRun'])
            self.assertEqual('0', report['actualCostUsd'])
            self.assertEqual(3, len(list(Path(output).iterdir())))
        self.assertEqual(before, snapshot())


if __name__ == '__main__':
    unittest.main()
