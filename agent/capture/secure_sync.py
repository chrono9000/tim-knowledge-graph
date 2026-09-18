"""Outbound-only, run-once synchronization. No scheduler or inbound listener."""
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .contract import validate, strict_json, encode, digest
from .service import Store

def un64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))

def private_path(path):
    path=Path(path).absolute()
    for part in [path,*path.parents]:
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise ValueError('Linked private path')
        if (part/'.git').exists(): raise ValueError('Runtime data must be outside Git')
    return path

def decrypt_jwe(value,private_key,key_id):
    if len(value)>60000:raise ValueError('Envelope oversized')
    header,wrapped,iv,cipher,tag=value.split('.')
    metadata=json.loads(un64(header))
    if metadata!={'alg':'RSA-OAEP-256','enc':'A256GCM','kid':key_id}:raise ValueError('Unexpected encryption parameters')
    key=private_key.decrypt(un64(wrapped),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    if len(un64(iv))!=12 or len(un64(tag))!=16 or len(key)!=32:raise ValueError('Invalid encryption bounds')
    return AESGCM(key).decrypt(un64(iv),un64(cipher)+un64(tag),header.encode())

def encrypted_backup(store,destination,key):
    """Snapshot SQLite plus private state. Caller supplies an independent destination."""
    dest=private_path(destination)
    if dest.exists() or len(key)!=32:raise ValueError('New backup path and 256-bit key required')
    root=store.path.parent
    if dest.is_relative_to(root):raise ValueError('Backup must be outside primary store')
    store.verify_history()
    with tempfile.TemporaryDirectory() as temp:
        snapshot=Path(temp)/'capture.sqlite3'
        store.backup(snapshot)
        memory=io.BytesIO()
        with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot,'capture.sqlite3')
            total=snapshot.stat().st_size
            for file in root.rglob('*'):
                private_path(file)
                if file.is_file() and file not in [store.path,Path(str(store.path)+'-wal'),Path(str(store.path)+'-shm')] and not file.name.endswith('.lock'):
                    if file.stat().st_size>25*1024*1024:raise ValueError('Backup entry too large')
                    total+=file.stat().st_size
                    if total>100*1024*1024:raise ValueError('Backup exceeds restore bound')
                    archive.write(file,file.relative_to(root).as_posix())
    nonce=secrets.token_bytes(12)
    sealed=b'FEOSBACKUP1'+nonce+AESGCM(key).encrypt(nonce,memory.getvalue(),b'FEOSBACKUP1')
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('xb') as output:
        output.write(sealed);output.flush();os.fsync(output.fileno())
    # Read-back authenticated verification before this backup can authorize ack.
    raw=dest.read_bytes()
    AESGCM(key).decrypt(raw[11:23],raw[23:],b'FEOSBACKUP1')
    return hashlib.sha256(raw).hexdigest()

def restore_backup(source,destination,key):
    target=private_path(destination)
    if target.exists():raise ValueError('Restore requires a new directory')
    raw=Path(source).read_bytes()
    if raw[:11]!=b'FEOSBACKUP1':raise ValueError('Invalid backup')
    data=AESGCM(key).decrypt(raw[11:23],raw[23:],b'FEOSBACKUP1')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(x.file_size for x in archive.infolist())>100*1024*1024:raise ValueError('Backup oversized')
        for name in archive.namelist():
            p=Path(name)
            if p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name:raise ValueError('Unsafe archive path')
        target.mkdir(parents=True)
        for item in archive.infolist():
            file=target/item.filename;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(archive.read(item))
    restored=Store(target/'capture.sqlite3');restored.verify_history();restored.set_enabled(False)
    return restored

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Redirect refused')

def https_transport(origin,path,body,headers):
    if not origin.startswith('https://') or urllib.parse.urlparse(origin).path not in {'','/'}:
        raise ValueError('HTTPS origin required')
    req=urllib.request.Request(origin.rstrip('/')+path,data=body,headers=headers,method='POST')
    with urllib.request.build_opener(NoRedirect).open(req,timeout=20) as response:
        raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise ValueError('Response oversized')
        return json.loads(raw)

class Poller:
    def __init__(self,store,origin,key_id,key,envelope_keys,private_key,recipient_id,backup_dir,backup_key,transport=https_transport):
        self.store=store;self.origin=origin;self.key_id=key_id;self.key=key
        self.envelope_keys=envelope_keys;self.private_key=private_key;self.recipient_id=recipient_id
        self.backup_dir=private_path(backup_dir);self.backup_key=backup_key;self.transport=transport
        if len(key)<32 or len(backup_key)!=32:raise ValueError('Invalid key size')
        if self.backup_dir.is_relative_to(store.path.parent):raise ValueError('Independent backup required')
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sync_receipts(id TEXT PRIMARY KEY,envelope_hash TEXT NOT NULL,backup_hash TEXT,acked INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sync_failures(id TEXT PRIMARY KEY,attempts INTEGER,next_at REAL,dead INTEGER);
                CREATE TABLE IF NOT EXISTS sync_cursor(id INTEGER PRIMARY KEY,position INTEGER NOT NULL);
                INSERT OR IGNORE INTO sync_cursor VALUES(1,0);
            """)
    def request(self,path,value):
        body=encode(value);stamp=str(int(time.time()));nonce=secrets.token_urlsafe(32)
        message='\n'.join(['POST',path,stamp,nonce,hashlib.sha256(body).hexdigest()]).encode()
        sig=base64.urlsafe_b64encode(hmac.digest(self.key,message,'sha256')).decode().rstrip('=')
        return self.transport(self.origin,path,body,{'Content-Type':'application/json','X-Key-Id':self.key_id,'X-Timestamp':stamp,'X-Nonce':nonce,'X-Signature':sig})
    def receive(self,item):
        raw=(item['receipt']+'\n'+item['envelope']).encode()
        key=self.envelope_keys[item['keyId']]
        if not hmac.compare_digest(hmac.digest(key,raw,'sha256'),un64(item['signature'])):raise ValueError('Bad envelope signature')
        packet=json.loads(decrypt_jwe(item['envelope'],self.private_key,self.recipient_id))
        if packet['receipt']!=item['receipt'] or packet['version']!=1 or hashlib.sha256(packet['deltaText'].encode()).hexdigest()!=packet['contentHash']:
            raise ValueError('Envelope integrity')
        delta=validate(strict_json(packet['deltaText'].encode()))
        with self.store.transaction() as db:
            if not db.execute('SELECT enabled FROM control').fetchone()[0]:raise ValueError('Paused')
            prior=db.execute('SELECT hash,principal FROM submissions WHERE id=?',(item['receipt'],)).fetchone()
            if prior:
                if prior['hash']!=digest(delta) or prior['principal']!=packet['principal']:raise ValueError('Receipt conflict')
            else:
                if packet['expires']<=time.time() or packet['received']>time.time()+300:raise ValueError('Expired delivery')
                db.execute('INSERT INTO submissions VALUES(?,?,?,?,?)',(item['receipt'],packet['principal'],encode(delta).decode(),digest(delta),packet['received']))
                db.execute("INSERT INTO jobs VALUES(?,'pending',0,0)",(item['receipt'],))
                self.store.audit(db,'hosted-received',item['receipt'])
            db.execute('INSERT OR IGNORE INTO sync_receipts(id,envelope_hash) VALUES(?,?)',(item['receipt'],hashlib.sha256(item['envelope'].encode()).hexdigest()))
        # Always make a fresh independent backup after a delivery/retry and before ack.
        backup=encrypted_backup(self.store,self.backup_dir/(secrets.token_hex(16)+'.feos'),self.backup_key)
        with self.store.transaction() as db:
            db.execute('UPDATE sync_receipts SET backup_hash=? WHERE id=?',(backup,item['receipt']))
        self.request('/sync/ack',{'receipt':item['receipt'],'backupHash':backup})
        with self.store.transaction() as db:
            db.execute('UPDATE sync_receipts SET acked=1 WHERE id=?',(item['receipt'],))
            db.execute('DELETE FROM sync_failures WHERE id=?',(item['receipt'],))
            self.store.audit(db,'hosted-acknowledged',item['receipt'])
    def run_once(self,clock=time.time):
        if not self.store.health()['enabled']:return {'paused':True,'received':0,'failed':0}
        counts={'received':0,'failed':0}
        with self.store.connection() as db:
            outage=db.execute("SELECT * FROM sync_failures WHERE id='transport'").fetchone()
        if outage and (outage['dead'] or outage['next_at']>clock()):return dict(counts,backoff=True)
        # Reconcile a lost acknowledgment response even if the host no longer lists it.
        with self.store.connection() as db:
            pending=db.execute('SELECT id,backup_hash FROM sync_receipts WHERE acked=0 AND backup_hash IS NOT NULL').fetchall()
            cursor=db.execute('SELECT position FROM sync_cursor WHERE id=1').fetchone()[0]
        for receipt in pending:
            try:
                self.request('/sync/ack',{'receipt':receipt['id'],'backupHash':receipt['backup_hash']})
                with self.store.transaction() as db:
                    db.execute('UPDATE sync_receipts SET acked=1 WHERE id=?',(receipt['id'],))
                    db.execute('DELETE FROM sync_failures WHERE id=?',(receipt['id'],))
                    self.store.audit(db,'hosted-ack-reconciled',receipt['id'])
            except Exception:
                self.failure('transport',clock());return dict(counts,failed=1)
        try:
            page=self.request('/sync/pull',{'after':cursor});items=page['items'];next_cursor=page.get('next',0)
        except Exception:
            self.failure('transport',clock());return dict(counts,failed=1)
        if not isinstance(items,list) or len(items)>20 or type(next_cursor)!=int or next_cursor<0:raise ValueError('Invalid page')
        with self.store.transaction() as db:db.execute("DELETE FROM sync_failures WHERE id='transport'")
        for item in items:
            identity=item.get('receipt','invalid')
            with self.store.connection() as db:r=db.execute('SELECT * FROM sync_failures WHERE id=?',(identity,)).fetchone()
            if r and (r['dead'] or r['next_at']>clock()):continue
            try:self.receive(item);counts['received']+=1
            except Exception:self.failure(identity,clock());counts['failed']+=1
        with self.store.transaction() as db:db.execute('UPDATE sync_cursor SET position=? WHERE id=1',(next_cursor,))
        return counts
    def failure(self,identity,at):
        with self.store.transaction() as db:
            row=db.execute('SELECT attempts FROM sync_failures WHERE id=?',(identity,)).fetchone()
            n=(row[0] if row else 0)+1
            db.execute('INSERT OR REPLACE INTO sync_failures VALUES(?,?,?,?)',(identity,n,at+min(3600,30*2**n),int(n>=5)))
            self.store.audit(db,'sync-failure',hashlib.sha256(identity.encode()).hexdigest())
    def retry(self,identity):
        with self.store.transaction() as db:
            db.execute('DELETE FROM sync_failures WHERE id=?',(identity,))
            self.store.audit(db,'manual-sync-retry',identity)
