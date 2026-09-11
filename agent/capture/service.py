"""Signed loopback intake and transactional, append-only private storage.

This HMAC boundary is for a trusted adapter, NOT ChatGPT OAuth authentication.
There is intentionally no approve, merge, publish, delete or remote admin route.
"""
import hashlib
import hmac
import json
import re
import sqlite3
import time
from contextlib import contextmanager, closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .contract import MAX_BYTES, digest, encode, idempotency_key, strict_json, validate

class Rejected(ValueError):
    def __init__(self,code,status=400):super().__init__(code);self.status=status

def signature(secret,path,stamp,nonce,key,body):
    message='\n'.join(['POST',path,str(stamp),nonce,key,hashlib.sha256(body).hexdigest()]).encode()
    return hmac.new(secret,message,hashlib.sha256).hexdigest()

def signed_headers(secret,delta,nonce,stamp=None):
    stamp=int(time.time()) if stamp is None else stamp
    body=encode(delta);key=idempotency_key(delta)
    return {'X-Key-Id':'local','X-Timestamp':str(stamp),'X-Nonce':nonce,'Idempotency-Key':key,
            'X-Signature':signature(secret,'/v1/deltas',stamp,nonce,key,body),'Content-Type':'application/json'}

class Store:
    def __init__(self,path):
        self.path=Path(path).resolve()
        if any((p/'.git').exists() for p in [self.path.parent,*self.path.parents]):raise ValueError('Capture storage must be outside every Git checkout')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
              INSERT OR IGNORE INTO control VALUES(1,0);
              CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, principal TEXT, payload TEXT, hash TEXT, received REAL);
              CREATE TABLE IF NOT EXISTS nonces(principal TEXT, nonce TEXT, PRIMARY KEY(principal,nonce));
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, status TEXT, attempts INTEGER, next_at REAL);
              CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, at REAL, kind TEXT, ref TEXT, previous TEXT, hash TEXT);
              CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, slot TEXT, fingerprint TEXT, payload TEXT);
              CREATE TABLE IF NOT EXISTS occurrences(submission TEXT, claim TEXT, proposal TEXT, PRIMARY KEY(submission,claim));
            ''')
            for table in ['submissions','nonces','audit','proposals','occurrences']:
                for verb in ['UPDATE','DELETE']:
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END")

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:yield db
        finally:db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:yield db;db.commit()
            except BaseException:db.rollback();raise

    def audit(self,db,kind,ref):
        previous=db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        previous=previous['hash'] if previous else ''
        at=time.time();value=digest([at,kind,ref,previous])
        db.execute('INSERT INTO audit(at,kind,ref,previous,hash) VALUES(?,?,?,?,?)',(at,kind,ref,previous,value))

    def set_enabled(self,enabled):
        with self.transaction() as db:
            db.execute('UPDATE control SET enabled=? WHERE id=1',(int(bool(enabled)),))
            self.audit(db,'enabled' if enabled else 'disabled','local-operator')

    def health(self):
        with self.connection() as db:
            outbox={}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='outbox'").fetchone():
                outbox={r['status']:r['n'] for r in db.execute('SELECT status,count(*) n FROM outbox GROUP BY status')}
            return {'enabled':bool(db.execute('SELECT enabled FROM control').fetchone()[0]),'deliveries':outbox,
                    'jobs':{r['status']:r['n'] for r in db.execute('SELECT status,count(*) n FROM jobs GROUP BY status')},
                    'proposals':db.execute('SELECT count(*) FROM proposals').fetchone()[0],
                    'lastAudit':db.execute('SELECT max(at) FROM audit').fetchone()[0],
                    'schedulerActive':False,'graphWriterAvailable':False}

    def accept(self,body,headers,keys,now=None):
        now=time.time() if now is None else now
        headers={k.lower():v for k,v in headers.items()}
        if len(body)>MAX_BYTES:raise Rejected('oversized',413)
        principal=headers.get('x-key-id','');secret=keys.get(principal)
        if secret is None or len(secret)<32:raise Rejected('unauthenticated',401)
        nonce=headers.get('x-nonce','');key=headers.get('idempotency-key','');stamp=headers.get('x-timestamp','')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{16,96}',nonce) or not re.fullmatch(r'[0-9a-f]{64}',key) or not stamp.isdigit():raise Rejected('invalid-auth-envelope',401)
        if abs(now-int(stamp))>300:raise Rejected('expired',401)
        expected=signature(secret,'/v1/deltas',stamp,nonce,key,body)
        if not hmac.compare_digest(expected,headers.get('x-signature','')):raise Rejected('bad-signature',401)
        try:delta=validate(strict_json(body))
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError):raise Rejected('malformed') from None
        if key!=idempotency_key(delta):raise Rejected('invalid-idempotency-key')
        identity=digest([principal,key]);content=digest(delta)
        with self.transaction() as db:
            if not db.execute('SELECT enabled FROM control').fetchone()[0]:raise Rejected('capture-disabled',503)
            if db.execute('SELECT 1 FROM nonces WHERE principal=? AND nonce=?',(principal,nonce)).fetchone():raise Rejected('replay',409)
            db.execute('INSERT INTO nonces VALUES(?,?)',(principal,nonce))
            previous=db.execute('SELECT hash FROM submissions WHERE id=?',(identity,)).fetchone()
            if previous:
                if previous['hash']!=content:raise Rejected('idempotency-conflict',409)
                self.audit(db,'duplicate-delivery',identity)
                return {'receipt':identity,'status':'already-queued'}
            # Local mock admission bound; production needs per-principal rate limiting.
            if db.execute('SELECT count(*) FROM jobs WHERE status!=?',('done',)).fetchone()[0]>=1000:raise Rejected('queue-full',503)
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?)',(identity,principal,encode(delta).decode(),content,now))
            db.execute('INSERT INTO jobs VALUES(?,?,0,0)',(identity,'pending'))
            self.audit(db,'received',identity)
        return {'receipt':identity,'status':'queued'}

    def retry(self,identity):
        with self.transaction() as db:
            result=db.execute("UPDATE jobs SET status='retry',attempts=0,next_at=0 WHERE id=? AND status='dead'",(identity,))
            if result.rowcount!=1:raise ValueError('Only dead jobs can be requeued')
            self.audit(db,'manual-retry',identity)

    def backup(self,destination):
        target=Path(destination).resolve()
        if target.exists() or any((p/'.git').exists() for p in target.parents):raise ValueError('Backup must be new and outside Git')
        target.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as source,closing(sqlite3.connect(target)) as dest:source.backup(dest)

    def verify_history(self):
        with self.connection() as db:
            previous=''
            for row in db.execute('SELECT * FROM audit ORDER BY seq'):
                if row['previous']!=previous or row['hash']!=digest([row['at'],row['kind'],row['ref'],previous]):raise ValueError('Audit integrity failure')
                previous=row['hash']
            for row in db.execute('SELECT payload,hash FROM submissions'):
                if digest(json.loads(row['payload']))!=row['hash']:raise ValueError('Evidence integrity failure')
        return {'integrity':'ok','auditHead':previous}

def make_server(store,keys,host='127.0.0.1',port=0):
    if host!='127.0.0.1':raise ValueError('Mock server binds only to 127.0.0.1')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # Never log request bodies or credentials.
        def do_POST(self):
            self.connection.settimeout(3)
            status=202
            try:
                if self.path!='/v1/deltas':raise Rejected('not-found',404)
                if self.headers.get('Origin') is not None:raise Rejected('browser-origin-forbidden',403)
                if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':raise Rejected('invalid-host',403)
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:raise Rejected('length-required',411)
                length=int(self.headers.get('Content-Length','-1'))
                if not 0<=length<=MAX_BYTES:raise Rejected('oversized',413)
                if self.headers.get('Content-Type')!='application/json':raise Rejected('content-type',415)
                for name in ['X-Key-Id','X-Timestamp','X-Nonce','Idempotency-Key','X-Signature']:
                    if len(self.headers.get_all(name,[]))!=1:raise Rejected('auth-header',401)
                body=self.rfile.read(length)
                if len(body)!=length:raise Rejected('incomplete-body')
                result=store.accept(body,dict(self.headers),keys)
            except Rejected as exc:status=exc.status;result={'error':str(exc)}
            except (ValueError,TimeoutError):status=400;result={'error':'invalid-request'}
            except Exception:status=503;result={'error':'unavailable'}
            body=encode(result);self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def do_GET(self):self.send_error(404)
        def do_DELETE(self):self.send_error(405)
    return ThreadingHTTPServer((host,port),Handler)
