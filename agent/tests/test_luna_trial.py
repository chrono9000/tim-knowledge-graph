import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal
from agent.trial import run_luna as r
from agent.trial import evaluate as e


class LunaTrialTests(unittest.TestCase):
    def test_frozen_answers_unchanged(self):
        cases, gold = r.suite()
        self.assertEqual(16, len(cases))
        self.assertEqual(16, len(gold))

    def test_missing_key_never_sends(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(r, 'post') as send:
            self.assertEqual('disabled-by-user', r.run()['status'])
            send.assert_not_called()

    def test_failure_reserves_and_prevents_retry(self):
        with tempfile.TemporaryDirectory() as d, patch.object(r, 'RUNTIME', Path(d)), patch.dict(os.environ, {'OPENAI_API_KEY': 'test-double'}), patch.object(r, 'post', side_effect=TimeoutError) as send:
            result = r._run_reserved()
            self.assertEqual(1, len(result['calls']))
            self.assertEqual('0.0112', result['reservedUsd'])
            self.assertEqual('stopped-validation-or-transport-error', result['status'])
            with self.assertRaises(FileExistsError):
                r._run_reserved()
            self.assertEqual(1, send.call_count)
            self.assertNotIn('test-double', (Path(d)/'ledger.json').read_text())

    def test_schema_failure_preserves_raw_and_stops(self):
        body = {'model': e.MODEL, 'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 50},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"wrong":true}'}]}]}
        raw = json.dumps(body).encode()
        with tempfile.TemporaryDirectory() as d, patch.object(r, 'RUNTIME', Path(d)), patch.dict(os.environ, {'OPENAI_API_KEY': 'test-double'}), patch.object(r, 'post', return_value=raw) as send:
            result = r._run_reserved()
            self.assertEqual(1, send.call_count)
            self.assertEqual(raw, (Path(d)/'01-raw.json').read_bytes())
            self.assertEqual('0.00008', result['actualCostUsd'])
            self.assertFalse((Path(d)/'01-review.json').exists())

    def test_usage_limits_and_cached_cost(self):
        self.assertEqual(Decimal('0.000062'), r.usage_cost({'usage': {'input_tokens': 100, 'output_tokens': 50, 'input_tokens_details': {'cached_tokens': 100}}}))
        with self.assertRaises(ValueError):
            r.usage_cost({'usage': {'input_tokens': 20001, 'output_tokens': 1}})
        for raw in ['{"a":1,"a":2}', '{"a":NaN}']:
            with self.assertRaises(ValueError):
                r.decode(raw)

    def test_requested_metrics(self):
        case = {'claims': [{'message_id':'m1', 'quote':'example', 'category':'decision', 'epistemic':'decision', 'attributes': {'subject':'Nora', 'deadlineLocal':'2026-09-08'}, 'uncertainties':['possible-conflict']}], 'relationships':[]}
        score = r.diagnostics(case, {'claims':[], 'relationships':[]})
        for field in ['speakerResolutionErrors', 'deadlineErrors', 'missedConflictFlags', 'incompleteClassifications']:
            self.assertEqual(1, score['additionalMetrics'][field])
