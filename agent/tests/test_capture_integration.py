"""Synthetic-only integration/restore/approval tests. No external network."""
import copy
import hashlib
import json
import secrets
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from agent.capture.service import Store,signed_headers
from agent.capture.contract import encode
from agent.capture.curator import run_once,review
from agent.capture.bridge import stage,snapshot,decide
from agent.capture.secure_sync import encrypted_backup,restore_backup,Poller
from agent.intake import IntakeConfig,load_private_master
from agent.safety import recover
from agent.tests.test_capture import example

def empty_graph():
    return {'schemaVersion':'1.0.0','title':'Synthetic public graph','generatedAt':'2026-09-16T12:00:00Z','categories':[{'id':'synthetic','label':'Synthetic','color':'#aaaaaa'}],'sources':[],'nodes':[],'edges':[]}

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'private/capture.sqlite3');self.store.set_enabled(True)
        self.public=self.root/'public/graph.json';self.public.parent.mkdir();self.public.write_text(json.dumps(empty_graph()))
        self.config=IntakeConfig(self.public,self.root/'private/master.json',self.root/'private/staging.json',self.root/'private/logs')
        self.key=secrets.token_bytes(32)
    def tearDown(self):self.temp.cleanup()
    def ingest(self,d=None):
        d=d or example()
        self.store.accept(encode(d),signed_headers(self.key,d,secrets.token_hex(16)),{'local':self.key})
        run_once(self.store);stage(self.store,self.config)
        return snapshot(self.config)
    def test_selected_private_approval_and_rejection_isolate_public(self):
        original=self.public.read_bytes();s=self.ingest()
        ids=[x['captureId'] for g in s['groups'].values() for x in g]
        self.assertFalse(self.config.private_graph_path.exists())
        decide(self.config,s['hash'],ids,'approve-private','Synthetic Reviewer')
        self.assertEqual(len(load_private_master(self.config)['nodes']),1)
        self.assertEqual(original,self.public.read_bytes())
        with self.assertRaisesRegex(ValueError,'Stale'):decide(self.config,s['hash'],ids,'approve-private','Synthetic Reviewer')
        d=example();d['claims'][0].update(id='new-risk',type='risk',value='A missed contract milestone could delay the project.')
        d['claims'][0]['materiality']['criterion']='risk';s=self.ingest(d)
        before=self.config.private_graph_path.read_bytes()
        decide(self.config,s['hash'],[x['captureId'] for g in s['groups'].values() for x in g],'reject','Synthetic Reviewer')
        self.assertEqual(before,self.config.private_graph_path.read_bytes());self.assertEqual(original,self.public.read_bytes())
    def test_relationship_dependencies_are_visible_and_valid(self):
        d=example();c=d['claims'][0];c.update(type='relationship',relationship={'type':'owner-of','source':'synthetic-ava','target':'juniper'});c['materiality']['criterion']='relationship'
        s=self.ingest(d);items=[x for g in s['groups'].values() for x in g]
        self.assertEqual(4,len(items[0]['records']))
        decide(self.config,s['hash'],[items[0]['captureId']],'approve-private','Synthetic Reviewer')
        graph=load_private_master(self.config);self.assertEqual(1,len(graph['edges']));self.assertEqual(2,len(graph['nodes']))
    def test_pause_and_bad_selections_and_public_action_rejected(self):
        s=self.ingest();ids=[x['captureId'] for g in s['groups'].values() for x in g]
        for action,selection in [('publish',ids),('approve-public',ids),('approve-private',[]),('approve-private',['unknown'])]:
            with self.assertRaises(ValueError):decide(self.config,s['hash'],selection,action,'Synthetic Reviewer')
        path=self.config.private_graph_path.parent/'workflow/STOP';path.parent.mkdir(exist_ok=True);path.touch()
        with self.assertRaises(ValueError):decide(self.config,s['hash'],ids,'approve-private','Synthetic Reviewer')
    def test_encrypted_independent_backup_restore_and_tamper(self):
        self.ingest();dest=self.root/'independent/backup.feos';key=secrets.token_bytes(32)
        h=encrypted_backup(self.store,dest,key);self.assertEqual(h,hashlib.sha256(dest.read_bytes()).hexdigest())
        self.assertNotIn(b'Juniper',dest.read_bytes())
        restored=restore_backup(dest,self.root/'restored',key);self.assertFalse(restored.health()['enabled']);self.assertEqual('ok',restored.verify_history()['integrity'])
        self.assertEqual(self.store.health()['proposals'],restored.health()['proposals'])
        with self.assertRaises(ValueError):encrypted_backup(self.store,self.root/'private/nested.feos',key)
        with self.assertRaises(Exception):restore_backup(dest,self.root/'wrong-key',secrets.token_bytes(32))
        dest.write_bytes(dest.read_bytes()[:-1]+bytes([dest.read_bytes()[-1]^1]))
        with self.assertRaises(Exception):restore_backup(dest,self.root/'tampered',key)
    def test_retry_and_outage_are_persistent_and_bounded(self):
        def outage(*args):raise OSError('synthetic outage')
        p=Poller(self.store,'https://synthetic.test','computer',self.key,{},None,'none',self.root/'independent',self.key,outage)
        self.assertEqual(1,p.run_once()['failed'])
        self.assertTrue(p.run_once()['backoff'])
        for _ in range(4):p.failure('transport',0)
        self.assertTrue(p.run_once()['backoff']);p.retry('transport')
        with self.store.connection() as db:self.assertIsNone(db.execute("SELECT * FROM sync_failures WHERE id='transport'").fetchone())
    def test_bridge_restaging_is_idempotent_and_keeps_full_metadata(self):
        self.ingest();before=self.config.staging_path.read_bytes();self.assertEqual([],stage(self.store,self.config))
        self.assertEqual(before,self.config.staging_path.read_bytes())
        s=snapshot(self.config);p=next(iter(s['groups'].values()))[0]['metadata']
        self.assertEqual('owner',p['assertedAuthority']);self.assertEqual('unknown',p['authority']);self.assertEqual(.5,p['confidence']);self.assertTrue(p['claim']['evidence'])

    def test_explicit_selected_supersession_preserves_previous_wording(self):
        s=self.ingest();first=next(iter(s['groups'].values()))[0]['captureId']
        decide(self.config,s['hash'],[first],'approve-private','Synthetic Reviewer')
        d=example();d['claims'][0].update(id='changed-2',type='change',supersedes=first,value='Use plain text for Juniper notes from now on.')
        d['claims'][0]['materiality']['criterion']='change'
        s=self.ingest(d);item=next(iter(s['groups'].values()))[0]
        self.assertTrue(item['changes'])
        decide(self.config,s['hash'],[item['captureId']],'approve-private','Synthetic Reviewer')
        graph=load_private_master(self.config);self.assertEqual(1,len(graph['nodes']))
        node=graph['nodes'][0];self.assertEqual(d['claims'][0]['value'],node['description'])
        self.assertIn(example()['claims'][0]['value'],[h['description'] for h in node['claimHistory']])

    def test_approval_journal_recovers_crash_without_public_write(self):
        s=self.ingest();identity=next(iter(s['groups'].values()))[0]['captureId'];before=self.public.read_bytes()
        with patch('agent.safety._finish',side_effect=OSError('synthetic disk interruption')):
            with self.assertRaises(OSError):decide(self.config,s['hash'],[identity],'approve-private','Synthetic Reviewer')
        recover(self.config)
        self.assertEqual(1,len(load_private_master(self.config)['nodes']))
        self.assertEqual(before,self.public.read_bytes())
        recover(self.config)
        self.assertEqual(1,len(load_private_master(self.config)['nodes']))

if __name__=='__main__':unittest.main()
