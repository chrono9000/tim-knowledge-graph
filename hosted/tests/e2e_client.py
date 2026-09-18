"""Only invoked by the local Node test server with ephemeral synthetic keys."""
import json,sys,urllib.request,base64,hashlib
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from agent.capture.service import Store
from agent.capture.secure_sync import Poller,un64,restore_backup
from agent.capture.curator import run_once
from agent.capture.bridge import stage,snapshot,decide
from agent.intake import IntakeConfig,load_private_master
from agent.tests.test_capture_integration import empty_graph

cfg=json.loads(Path(sys.argv[1]).read_text())
root=Path(cfg['root']);store=Store(root/'private/capture.sqlite3');store.set_enabled(True)
lost_ack=[False]
def transport(origin,path,body,headers):
    # Loopback test adapter only. The production transport requires HTTPS and refuses redirects.
    request=urllib.request.Request('http://127.0.0.1:'+str(cfg['port'])+path,data=body,headers=headers,method='POST')
    with urllib.request.urlopen(request,timeout=10) as response:result=json.load(response)
    if path=='/sync/ack' and not lost_ack[0]:
        lost_ack[0]=True
        raise OSError('Synthetic response loss after host committed acknowledgment')
    return result
poller=Poller(store,'https://intake.test','computer',un64(cfg['sync']),{'test-hmac':un64(cfg['sign'])},serialization.load_der_private_key(un64(cfg['private']),None),'test-rsa',root/'independent',un64(cfg['backup']),transport)
assert poller.run_once()=={'received':1,'failed':1}
assert poller.run_once()=={'received':0,'failed':0}
with store.connection() as db:assert db.execute('SELECT count(*) FROM sync_receipts WHERE acked=1').fetchone()[0]==2
assert run_once(store)['processed']==2
public=root/'public.json';public.write_text(json.dumps(empty_graph()));before=public.read_bytes()
config=IntakeConfig(public,root/'private/master.json',root/'private/staging.json',root/'private/logs')
stage(store,config);s=snapshot(config);items=[p for g in s['groups'].values() for p in g];assert len(items)==2
assert not config.private_graph_path.exists()
decide(config,s['hash'],[items[0]['captureId']],'approve-private','Synthetic Reviewer')
s=snapshot(config);rest=[p for g in s['groups'].values() for p in g]
decide(config,s['hash'],[rest[0]['captureId']],'reject','Synthetic Reviewer')
assert len(load_private_master(config)['nodes'])==1
assert public.read_bytes()==before
backup=next((root/'independent').glob('*.feos'))
assert restore_backup(backup,root/'restore',un64(cfg['backup'])).verify_history()['integrity']=='ok'
assert store.verify_history()['integrity']=='ok'
print(json.dumps({'authenticatedSubmissions':2,'retrieved':2,'curated':2,'approvedSyntheticItems':1,'rejectedSyntheticItems':1,'publicUnchanged':True,'encryptedRestoreVerified':True}))
