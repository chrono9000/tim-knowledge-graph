"""Adapter-side durable delivery outbox. Transport is injected; no network enabled here."""
import json
import secrets
import time
from .contract import digest,encode,idempotency_key,validate
from .service import Rejected,signed_headers

class Outbox:
    def __init__(self,store):
        self.store=store
        with store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,payload TEXT,status TEXT,attempts INTEGER,next_at REAL)')

    def enqueue(self,delta):
        validate(delta);key=idempotency_key(delta)
        with self.store.transaction() as db:
            previous=db.execute('SELECT payload FROM outbox WHERE id=?',(key,)).fetchone()
            if previous and digest(json.loads(previous[0]))!=digest(delta):raise ValueError('Idempotency conflict')
            db.execute("INSERT OR IGNORE INTO outbox VALUES(?,?,'pending',0,0)",(key,encode(delta).decode()))
            self.store.audit(db,'outbox-enqueued',key)
        return key

    def flush(self,secret,transport,now=None):
        now=int(time.time()) if now is None else now
        if not self.store.health()['enabled']:return 0
        with self.store.connection() as db:
            items=db.execute("SELECT * FROM outbox WHERE status IN ('pending','retry') AND next_at<=?",(now,)).fetchall()
        delivered=0
        for item in items:
            delta=json.loads(item['payload']);status='delivered';attempts=item['attempts']+1
            try:
                result=transport(encode(delta),signed_headers(secret,delta,secrets.token_hex(16),now))
                if result.get('status') not in {'queued','already-queued'}:raise ValueError('Invalid receipt')
                delivered+=1
            except Rejected as exc:
                status='retry' if exc.status>=500 and attempts<5 else 'dead'
            except (OSError,TimeoutError):status='retry' if attempts<5 else 'dead'
            except Exception:status='dead'
            with self.store.transaction() as db:
                db.execute('UPDATE outbox SET status=?,attempts=?,next_at=? WHERE id=?',(status,attempts,now+min(3600,30*2**attempts),item['id']))
                self.store.audit(db,'outbox-'+status,item['id'])
        return delivered

    def retry(self,key):
        with self.store.transaction() as db:
            result=db.execute("UPDATE outbox SET status='retry',attempts=0,next_at=0 WHERE id=? AND status='dead'",(key,))
            if result.rowcount!=1:raise ValueError('Only dead deliveries can be requeued')
            self.store.audit(db,'outbox-manual-retry',key)
