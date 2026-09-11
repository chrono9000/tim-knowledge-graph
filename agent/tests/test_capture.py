import copy
import http.client
import json
import secrets
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from agent.capture.contract import encode,validate,idempotency_key,strict_json
from agent.capture.service import Store,Rejected,signed_headers,make_server
from agent.capture.curator import run_once,review
from agent.capture.client import Outbox

def example():
    return {'version':1,'conversationId':'synthetic-chat-1','topic':'Project Juniper','sourceTimestamp':'2026-09-10T12:00:00Z',
        'entities':[{'id':'synthetic-ava','label':'Ava Example','type':'person'},{'id':'juniper','label':'Project Juniper','type':'project'}],
        'claims':[{'id':'message-1-format','type':'preference','subject':'juniper','scope':'notes','predicate':'format','value':'Use structured tables for Juniper notes.',
            'speaker':{'role':'user','identity':'synthetic-ava'},'confidence':.9,'authority':'owner',
            'materiality':{'criterion':'preference','durability':'durable','reason':'A reusable project documentation preference.'},
            'owner':'synthetic-ava','due':{'date':None,'time':None,'timezone':'UTC','ambiguity':'none'},'supersedes':None,'relationship':None,
            'sourceRef':'synthetic-chat-1/message-1','evidence':'I prefer structured tables for Juniper notes.','uncertainty':[]}]}

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'private/capture.sqlite3')
        self.key=secrets.token_bytes(32);self.keys={'local':self.key};self.store.set_enabled(True)
    def tearDown(self):self.temp.cleanup()
    def send(self,delta=None,nonce=None,stamp=1000):
        delta=delta or example();return self.store.accept(encode(delta),signed_headers(self.key,delta,nonce or secrets.token_hex(16),stamp),self.keys,now=1000)
    def test_valid_and_retry_idempotency(self):
        a=self.send();b=self.send();self.assertEqual(a['receipt'],b['receipt']);self.assertEqual('already-queued',b['status'])
        self.assertEqual(1,run_once(self.store)['processed']);self.assertEqual(0,run_once(self.store)['processed'])
    def test_unsigned_bad_signature_expired_replay(self):
        d=example();headers=signed_headers(self.key,d,'a'*32,1000)
        for change in [{},dict(headers,**{'X-Signature':'0'*64}),dict(headers,**{'X-Timestamp':'1'})]:
            with self.assertRaises(Rejected):self.store.accept(encode(d),change,self.keys,now=1000)
        self.store.accept(encode(d),headers,self.keys,now=1000)
        with self.assertRaisesRegex(Rejected,'replay'):self.store.accept(encode(d),headers,self.keys,now=1000)
    def test_changed_body_same_id_rejected(self):
        self.send();d=example();d['claims'][0]['value']='Changed content'
        with self.assertRaisesRegex(Rejected,'idempotency-conflict'):self.send(d)
    def test_bounds_and_strict_json(self):
        for raw in [b'{"x":1,"x":2}',b'{"x":NaN}',b' '*32769]:
            with self.assertRaises(ValueError):strict_json(raw)
        d=example();d['transcript']='forbidden'
        with self.assertRaises(ValueError):validate(d)
        d=example();d['claims'][0]['evidence']='x'*401
        with self.assertRaises(ValueError):validate(d)
    def test_owner_and_endpoint_validation(self):
        d=example();d['claims'][0].update(type='decision',owner=None)
        with self.assertRaises(ValueError):validate(d)
        d=example();d['claims'][0].update(type='relationship',relationship={'type':'owner-of','source':'juniper','target':'synthetic-ava'})
        with self.assertRaises(ValueError):validate(d)
    def test_kill_switch_stops_capture_and_curator(self):
        self.send();self.store.set_enabled(False)
        with self.assertRaisesRegex(Rejected,'disabled'):self.send()
        self.assertEqual(0,run_once(self.store)['processed'])
    def test_new_store_disabled(self):self.assertFalse(Store(self.root/'second.sqlite3').health()['enabled'])
    def test_dates_ambiguity_and_structured_commitment(self):
        d=example();c=d['claims'][0];c.update(type='commitment',value='I will send the Juniper brief tomorrow.')
        c['materiality']['criterion']='commitment';c['due'].update(date='2026-09-11',time='10:00')
        self.send(d);run_once(self.store);self.assertEqual(1,review(self.store)['recommendedItems'])
        c['due']['timezone']=None
        with self.assertRaises(ValueError):validate(d)
        c['due'].update(date=None,time=None,ambiguity='timezone')
        with self.assertRaises(ValueError):validate(d)
        c['uncertainty']=[{'field':'due','reason':'No source timezone is available to resolve tomorrow.'}]
        validate(d)
    def test_explicit_change_is_review_flag_not_graph_mutation(self):
        self.send();run_once(self.store);target=next(iter(review(self.store,expanded=True)['groups'].values()))[0]['id']
        d=example();d['claims'][0].update(id='revision-2',type='change',supersedes=target,value='I now prefer plain text instead of tables.')
        d['claims'][0]['materiality']['criterion']='change';self.send(d);run_once(self.store)
        p=next(iter(review(self.store)['groups'].values()))[0]
        self.assertEqual('possible-supersession',p['flags'][0]['code']);self.assertFalse(p['approvalEligible'])
    def test_body_tampering_and_future_signatures(self):
        d=example();headers=signed_headers(self.key,d,'b'*32,1000);d['topic']='Changed topic'
        with self.assertRaisesRegex(Rejected,'signature'):self.store.accept(encode(d),headers,self.keys,now=1000)
        headers=signed_headers(self.key,d,'b'*32,2000)
        with self.assertRaisesRegex(Rejected,'expired'):self.store.accept(encode(d),headers,self.keys,now=1000)
    def test_outside_git_required(self):
        (self.root/'.git').mkdir()
        with self.assertRaises(ValueError):Store(self.root/'bad.sqlite3')
    def test_temporary_context_and_recommendation_not_numbered(self):
        d=example()
        for i,value in enumerate(['Someone was waiting for microphone checks.','The parcel just arrived on my desk.','I recommend decorative borders.']):
            c=copy.deepcopy(d['claims'][0]);c.update(id=str(i),type='recommendation' if i==2 else 'context',value=value,evidence=value)
            c['materiality'].update(criterion='context',durability='temporary');d['claims'].append(c)
        self.send(d);run_once(self.store);r=review(self.store,expanded=True)
        self.assertEqual(1,r['expandedItems']);self.assertEqual(3,len(next(iter(r['groups'].values()))[0]['supportingEvidence']))
    def test_material_delay_preserved(self):
        d=example();c=d['claims'][0];c.update(type='risk',value='Waiting for microphone checks blocks the Juniper launch.')
        c['materiality'].update(criterion='risk',durability='temporary');self.send(d);run_once(self.store)
        self.assertEqual(1,review(self.store)['recommendedItems'])
    def test_cross_conversation_dedup_and_conflict(self):
        self.send();run_once(self.store)
        d=example();d['conversationId']='synthetic-chat-2';self.send(d);run_once(self.store)
        self.assertEqual(1,review(self.store,expanded=True)['expandedItems'])
        d['claims'][0].update(id='changed',value='Use plain text for Juniper notes.');self.send(d);run_once(self.store)
        result=review(self.store);self.assertEqual(1,result['recommendedItems'])
        p=next(iter(result['groups'].values()))[0];self.assertEqual('possible-conflict',p['flags'][0]['code'])
        self.assertIn('CONFLICT-001',p['policyDecision']['ruleIds']);self.assertEqual('unknown',p['authority']);self.assertEqual(.5,p['confidence'])
    def test_different_people_not_conflicts(self):
        self.send();run_once(self.store);d=example();d['conversationId']='synthetic-other'
        d['entities'][0]['id']='synthetic-ben';d['claims'][0]['owner']='synthetic-ben';d['claims'][0]['speaker']['identity']='synthetic-ben';d['claims'][0]['value']='Use plain text.'
        self.send(d);run_once(self.store);self.assertEqual(0,review(self.store)['recommendedItems'])
    def test_atomic_failure_backoff_and_recovery(self):
        receipt=self.send()['receipt']
        def fail(delta,db,principal):
            db.execute("INSERT INTO proposals VALUES('partial','x','x','{}')");raise RuntimeError('private error text')
        for now in [1000,2000,3000]:run_once(self.store,now=now,processor=fail)
        self.assertEqual({'dead':1},self.store.health()['jobs']);self.assertEqual(0,self.store.health()['proposals'])
        self.store.retry(receipt);self.assertEqual(1,run_once(self.store,now=4000)['processed'])
    def test_append_only_backup_and_restart(self):
        self.send();run_once(self.store)
        with self.store.connection() as db:
            for table in ['submissions','audit','proposals','occurrences','nonces']:
                with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM '+table)
        destination=self.root/'backup.sqlite3';self.store.backup(destination)
        self.assertEqual(self.store.health(),Store(destination).health())
        self.assertEqual('ok',Store(destination).verify_history()['integrity'])
    def test_durable_preference_not_lost_to_keyword(self):
        d=example();d['claims'][0]['value']='I prefer coffee for recurring project meetings.'
        self.send(d);run_once(self.store);self.assertEqual(1,review(self.store,expanded=True)['expandedItems'])
    def test_outbox_recovers_lost_ack(self):
        outbox=Outbox(self.store);outbox.enqueue(example())
        def lost(body,headers):
            self.store.accept(body,headers,self.keys,now=1000);raise TimeoutError()
        self.assertEqual(0,outbox.flush(self.key,lost,now=1000))
        self.assertEqual(1,outbox.flush(self.key,lambda b,h:self.store.accept(b,h,self.keys,now=1100),now=1100))
        self.assertEqual(1,run_once(self.store)['processed'])
    def test_concurrent_duplicate_requests_single_submission(self):
        failures=[]
        def send():
            try:self.send()
            except Exception as e:failures.append(str(e))
        threads=[threading.Thread(target=send) for _ in range(5)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertFalse(failures);self.assertEqual({'pending':1},self.store.health()['jobs'])
    def test_http_loopback_only_and_no_control_routes(self):
        with self.assertRaises(ValueError):make_server(self.store,self.keys,host='0.0.0.0')
        server=make_server(self.store,self.keys);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            d=example();headers=signed_headers(self.key,d,secrets.token_hex(16));conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST','/v1/deltas',encode(d),headers);response=conn.getresponse();self.assertEqual(202,response.status);response.read();conn.close()
            for route in ['/approve','/publish','/health','/pause']:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);conn.request('POST',route,b'{}');response=conn.getresponse();self.assertEqual(404,response.status);response.read();conn.close()
        finally:server.shutdown();server.server_close();thread.join()
