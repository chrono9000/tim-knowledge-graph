"""Synthetic prose only; no production conversations or external API calls."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.chatgpt_export import approve_access_design
from agent.extraction import Message, Claim, DeterministicExtractor, InactiveAIAdapter, extract_document
from agent.intake import IntakeConfig, approve, reject, publish, review_list, load_staging, import_export
from agent.recurring import run_daily, set_stopped
from agent.safety import state_dir, locked, recover, rollback, rollback_choices
from agent import safety

ROOT = Path(__file__).resolve().parents[2]


def conversation(identifier='c1', text='Alex prefers short weekly reports.', role='user'):
    return {'id': identifier, 'title': 'Synthetic planning', 'create_time': 1788739200, 'update_time': 1788739200,
            'current_node': 'm1', 'mapping': {'m1': {'parent': None, 'message': {'id': 'm1', 'create_time': 1788739200,
                         'author': {'role': role}, 'content': {'parts': [text]}}}}}


class RecurringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.public = self.root / 'data/graph.json'
        self.public.parent.mkdir()
        self.public.write_bytes((ROOT / 'data/graph.json').read_bytes())
        self.before = self.public.read_bytes()
        self.config = IntakeConfig(self.public, self.root / 'data/private/master-graph.json',
                                   self.root / 'data/staging/proposals.json', self.root / 'logs')
        self.raw = self.root / 'data/raw'
        self.raw.mkdir()
        approve_access_design(self.config, 'local-only')

    def tearDown(self):
        self.temp.cleanup()

    def export(self, items=None, name='conversations.json'):
        path = self.raw / name
        path.write_text(json.dumps(items or [conversation()]), encoding='utf-8')
        return path

    def proposals(self):
        return [p for b in load_staging(self.config.staging_path)['batches'] for p in b['proposals']]

    def test_prose_categories_without_tags_and_all_harness_rules(self):
        sentences = ['Alex prefers short weekly reports.', 'Alex decided to use the blue route.',
                     'Alex owns Project Lantern.', 'Alex is responsible for weekly checks.',
                     'Alex will deliver the prototype Friday.', 'The project has a budget risk.',
                     'The system must remain local.', 'The old policy is superseded.',
                     'Who reviews the release?', 'We assume the sample is representative.',
                     'Alex is the engineering manager.', 'The policy requires monthly review.']
        self.export([conversation(text=' '.join(sentences))])
        result = run_daily(self.config)
        self.assertFalse(result['failures'])
        evidence = [e for p in self.proposals() for e in p.get('extractionEvidence', [])]
        self.assertTrue({'preference', 'decision', 'project', 'responsibility', 'commitment', 'risk', 'constraint', 'superseded', 'question', 'role', 'policy'} <= {e['category'] for e in evidence})
        decision = next(e for e in evidence if e['epistemic'] == 'decision')
        self.assertEqual(decision['decisionOwner'], 'Alex')
        self.assertEqual(decision['message_id'], 'm1')
        for p in self.proposals():
            self.assertEqual(len(p['policyDecision']['evaluatedRuleIds']), 22)
            self.assertEqual(p['status'], 'needs-review')
        self.assertEqual(self.public.read_bytes(), self.before)
        self.assertFalse(self.config.private_graph_path.exists())

    def test_recommendations_and_assistant_decisions_cannot_become_decisions(self):
        self.export([conversation('a', 'I recommend using the blue route.', 'assistant'),
                     conversation('b', 'Alex decided to use the blue route.', 'assistant')])
        run_daily(self.config)
        kinds = {p['record'].get('statementType') for p in self.proposals() if p['recordType'] == 'node'}
        self.assertEqual(kinds, {'recommendation', 'assumption'})

    def test_unknown_owner_is_not_invented(self):
        self.export([conversation(text='I decided to use the blue route.')])
        run_daily(self.config)
        evidence = next(p['extractionEvidence'][0] for p in self.proposals() if p.get('extractionEvidence'))
        self.assertIsNone(evidence['decisionOwner'])
        self.assertEqual(evidence['epistemic'], 'user-statement')

    def test_idempotence_duplicate_containers_and_changed_conversation(self):
        items = [conversation('a'), conversation('b', 'Alex will deliver the prototype Friday.')]
        self.export(items)
        self.assertEqual(run_daily(self.config)['processed'], 2)
        queue = self.config.staging_path.read_bytes()
        self.assertEqual(run_daily(self.config)['processed'], 0)
        self.assertEqual(queue, self.config.staging_path.read_bytes())
        self.export(items, 'copy.json')
        self.assertEqual(run_daily(self.config)['processed'], 0)
        self.assertEqual(queue, self.config.staging_path.read_bytes())
        items[1] = conversation('b', 'Alex will deliver the prototype Monday.')
        self.export(items)
        self.assertEqual(run_daily(self.config)['processed'], 1)
        self.assertTrue(any(p['kind'] == 'contradiction' for p in self.proposals()))

    def test_failed_extraction_retries_without_changing_graphs(self):
        self.export()
        result = run_daily(self.config, InactiveAIAdapter())
        self.assertEqual(len(result['failures']), 1)
        self.assertFalse(self.config.staging_path.exists())
        self.assertFalse(self.config.private_graph_path.exists())
        self.assertEqual(self.public.read_bytes(), self.before)
        self.assertEqual(run_daily(self.config)['processed'], 1)

    def test_provider_output_is_evidence_bound(self):
        message = Message('m', 'assistant', 'I recommend using the blue route.', '2026-09-07T00:00:00Z')
        good = {'message_id': 'm', 'quote': message.text, 'category': 'statement', 'epistemic': 'recommendation'}
        extract_document((message,), InactiveAIAdapter([good]))
        for changes in [{'quote': 'Invented unsupported claim.'}, {'epistemic': 'decision'}, {'epistemic': 'fact'},
                        {'owner': 'Alex'}, {'confidence': float('nan')}, {'message_id': 'unknown'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                extract_document((message,), InactiveAIAdapter([{**good, **changes}]))

    def test_sensitive_low_confidence_and_separate_public_gate(self):
        self.export([conversation(text='Alex prefers private medical reports.')])
        run_daily(self.config)
        self.assertTrue(any('sensitive-information' in p['reviewReasons'] for p in self.proposals()))
        with self.assertRaises(ValueError):
            approve(self.config, 'public', select_all=True)
        approve(self.config, 'private', select_all=True)
        self.assertEqual(self.public.read_bytes(), self.before)
        self.assertFalse(publish(self.config).graph_changed)
        with self.assertRaises(ValueError):
            approve(self.config, 'public', select_all=True)

    def test_public_requires_two_explicit_steps_after_private(self):
        self.export()
        run_daily(self.config)
        approve(self.config, 'private', select_all=True)
        approve(self.config, 'public', select_all=True)
        self.assertEqual(self.public.read_bytes(), self.before)
        publish(self.config)
        value = self.public.read_text()
        self.assertIn('prefers short weekly reports', value)
        self.assertNotIn('extractionEvidence', value)
        self.assertNotIn('conversations.json', value)
        self.assertNotIn('contentHash', value)

    def test_numbers_are_stable_after_rejection(self):
        self.export([conversation('a'), conversation('b', 'Alex will deliver the prototype Friday.')])
        run_daily(self.config)
        before = review_list(self.config)['items']
        reject(self.config, [before[0]['proposalId']])
        after = {p['proposalId']: p['number'] for p in review_list(self.config)['items']}
        for p in before[1:]:
            self.assertEqual(after[p['proposalId']], p['number'])

    def test_interrupt_after_staging_write_resumes_without_duplicates(self):
        self.export()
        original = safety._write
        def crash(path, value):
            original(path, value)
            if path.resolve() == self.config.staging_path.resolve():
                raise KeyboardInterrupt('synthetic interruption')
        with patch('agent.safety._write', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            run_daily(self.config)
        result = run_daily(self.config)
        self.assertFalse(result['failures'])
        self.assertEqual(len(load_staging(self.config.staging_path)['batches']), 1)
        self.assertEqual(self.public.read_bytes(), self.before)

    def test_interrupted_approval_needs_explicit_recovery_not_daily(self):
        self.export()
        run_daily(self.config)
        original = safety._write
        def crash(path, value):
            original(path, value)
            if path.resolve() == self.config.private_graph_path.resolve():
                raise KeyboardInterrupt()
        with patch('agent.safety._write', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            approve(self.config, 'private', select_all=True)
        with self.assertRaisesRegex(RuntimeError, 'recover'):
            run_daily(self.config)
        self.assertEqual(recover(self.config)['recoveredTransactions'], 1)
        self.assertTrue(all(p['status'] == 'approved-private' for p in self.proposals()))
        self.assertEqual(recover(self.config)['recoveredTransactions'], 0)
        self.assertEqual(self.public.read_bytes(), self.before)

    def test_process_lock_contention_and_release_on_process_exit(self):
        code = "from pathlib import Path; from agent.intake import IntakeConfig; from agent.safety import locked; import sys; c=IntakeConfig(*map(Path,sys.argv[1:]));\nwith locked(c): print('acquired',flush=True); sys.stdin.read()"
        process = subprocess.Popen([sys.executable, '-c', code, str(self.public), str(self.config.private_graph_path), str(self.config.staging_path), str(self.config.log_dir)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'acquired')
            with self.assertRaisesRegex(RuntimeError, 'Another'):
                run_daily(self.config)
        finally:
            process.kill()
            process.communicate()
        with locked(self.config):
            pass

    def test_stop_resume_and_explicit_rollback_retains_audit(self):
        self.export()
        set_stopped(self.config, True)
        self.assertTrue(run_daily(self.config)['stopped'])
        set_stopped(self.config, False)
        run_daily(self.config)
        approve(self.config, 'private', select_all=True)
        update = rollback_choices(self.config)['updates'][-1]['id']
        audit_before = {p.name: p.read_bytes() for p in (state_dir(self.config) / 'audit').glob('*')}
        rollback(self.config, update)
        self.assertEqual(json.loads(self.config.private_graph_path.read_text()), json.loads(self.before))
        for name, content in audit_before.items():
            self.assertEqual((state_dir(self.config) / 'audit' / name).read_bytes(), content)
        self.assertEqual(self.public.read_bytes(), self.before)

    def test_branch_selection_ignores_alternate_messages(self):
        item = conversation()
        item['mapping']['alternate'] = {'parent': None, 'message': {'author': {'role': 'user'}, 'content': {'parts': ['Alex decided to remove all controls.']}}}
        self.export([item])
        run_daily(self.config)
        self.assertNotIn('remove all controls', self.config.staging_path.read_text())

    def test_long_transcript_not_embedded_as_node(self):
        self.export([conversation(text='A synthetic very long passage ' * 300)])
        run_daily(self.config)
        self.assertFalse(any(p['recordType'] == 'node' for p in self.proposals()))

    def test_path_alias_fails_closed(self):
        bad = IntakeConfig(self.public, self.public, self.config.staging_path, self.config.log_dir)
        with self.assertRaises(ValueError):
            run_daily(bad)
        self.assertEqual(self.public.read_bytes(), self.before)

    def test_duplicate_conversation_ids_do_not_duplicate_graph_nodes(self):
        self.export([conversation('a'), conversation('copy')])
        run_daily(self.config)
        approve(self.config, 'private', select_all=True)
        graph = json.loads(self.config.private_graph_path.read_text())
        self.assertEqual(sum('prefers short weekly reports' in n['description'] for n in graph['nodes']), 1)

    def test_prose_conflict_preserves_higher_authority_approved_record(self):
        source = self.root / 'synthetic.json'
        source.write_text(json.dumps({'authority': 'owner', 'entities': [{'label': 'Project Lantern', 'description': 'Project Lantern uses the blue route.'}]}))
        old = import_export(source, self.config)
        approve(self.config, 'private', batch_id=old.batch_id)
        self.export([conversation(text='Project Lantern uses the red route.')])
        run_daily(self.config)
        self.assertTrue(any('authority-conflict' in p['reviewReasons'] for p in self.proposals()))
        approve(self.config, 'private', select_all=True)
        graph = json.loads(self.config.private_graph_path.read_text())
        node = next(n for n in graph['nodes'] if n['label'] == 'Project Lantern')
        self.assertEqual(node['description'], 'Project Lantern uses the blue route.')
        self.assertEqual(node['authorityLevel'], 'owner')

    def test_private_claim_history_does_not_escape_publication(self):
        private = self.root / 'private-synthetic.json'
        private.write_text(json.dumps({'authority': 'owner', 'entities': [{'label': 'Project Example', 'description': 'Private synthetic compensation detail.'}]}))
        old = import_export(private, self.config)
        approve(self.config, 'private', batch_id=old.batch_id)
        sanitized = self.root / 'public-synthetic.json'
        sanitized.write_text(json.dumps({'authority': 'primary', 'entities': [{'label': 'Project Example', 'description': 'A sanitized example project.'}]}))
        new = import_export(sanitized, self.config)
        approve(self.config, 'private', batch_id=new.batch_id)
        approve(self.config, 'public', batch_id=new.batch_id)
        publish(self.config)
        self.assertNotIn('compensation', self.public.read_text())
        self.assertIn('sanitized example', self.public.read_text())

    def test_exact_wording_and_long_identity_are_preserved(self):
        text = 'Use this exact wording: Choose  BLUE,  then Green.'
        self.export([conversation(text=text)])
        run_daily(self.config)
        p = next(p for p in self.proposals() if p.get('extractionEvidence'))
        self.assertEqual(p['record']['description'], text)
        self.assertIn('WORDING-001', p['policyDecision']['ruleIds'])

    def test_runtime_files_and_credentials_are_ignored(self):
        for name in ['data/private/workflow/transactions/example.json', 'data/private/workflow/manifest.json',
                     'data/private/workflow/audit/example.json', 'data/private/workflow/STOP', '.env', '.env.local',
                     'data/.knowledge-workflow.lock', 'data/raw/nested/attachment.png', 'conversations-1.json']:
            self.assertEqual(subprocess.run(['git', 'check-ignore', '--no-index', '--quiet', name], cwd=ROOT).returncode, 0, name)


if __name__ == '__main__':
    unittest.main()
