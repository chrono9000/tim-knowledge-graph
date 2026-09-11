"""Explicit local run-once curator. Never scheduled and never writes a graph."""
import json
import time
from .contract import digest, encode, validate
from ..harness import load_harness, evaluate_proposal
from ..materiality import classify_materiality
from ..ingest import canonical_text

def curate(delta,db,principal):
    validate(delta)
    entities={e['id']:e for e in delta['entities']};output=[]
    for c in delta['claims']:
        category={'ownership':'responsibility','change':'superseded','unresolved-question':'question'}.get(c['type'],c['type'])
        gate=classify_materiality(c['value'],category)
        incidental=not gate['standalone'] and c['type'] in {'fact','entity','project','system','context'}
        evidence_only=(incidental or c['type'] in {'context','recommendation'} or c['materiality']['criterion']=='context')
        if evidence_only:
            output.append((c,None));continue
        # Identity and scope prevent one person's preferences becoming another's conflict.
        slot=digest([principal,c['subject'],canonical_text(c['scope']),canonical_text(c['predicate']),c['owner'] or c['speaker']['identity']])
        fingerprint=digest([slot,canonical_text(c['value']),c['type'],c['due'],c['relationship'],c['supersedes']])
        prior=db.execute('SELECT id,payload FROM proposals WHERE fingerprint=?',(fingerprint,)).fetchone()
        if prior:
            output.append((c,{'duplicate':prior['id']}));continue
        others=db.execute('SELECT id,payload FROM proposals WHERE slot=?',(slot,)).fetchall()
        flags=[];reasons=[]
        if c['supersedes']:
            target=db.execute('SELECT slot FROM proposals WHERE id=?',(c['supersedes'],)).fetchone()
            flags.append({'code':'possible-supersession' if target and target['slot']==slot else 'unresolved-change-target','target':c['supersedes']})
            reasons.append('possible-supersession')
        elif others:
            flags.append({'code':'possible-conflict','targets':[r['id'] for r in others]})
            reasons.append('possible-contradiction')
        high=c['type'] in {'decision','change','ownership','risk'} or bool(c['supersedes']) or bool(flags) or gate['priority']=='high'
        if c['type']=='commitment' and (c['due']['date'] or c['due']['ambiguity']!='none'):high=True
        # Transport auth identifies a sender, never establishes FEOS owner authority.
        confidence=min(.5,c['confidence']);reasons.append('low-confidence')
        record={'label':entities[c['subject']]['label'],'description':c['value'],
                'entityType':entities[c['subject']]['type'],'statementType':{'change':'superseded'}.get(c['type'],c['type'] if c['type'] in load_harness()['statementTypes'] else 'fact')}
        policy=evaluate_proposal(load_harness(),record,{'authorityLevel':'unknown'},reasons,
                                 previous=json.loads(others[0]['payload'])['record'] if others else None)
        proposal={'id':fingerprint,'status':'needs-review','approvalEligible':False,'classification':'private',
                  'record':record,'claim':c,'subject':entities[c['subject']],
                  'topic':delta['topic'],'conversationId':delta['conversationId'],'sourceTimestamp':delta['sourceTimestamp'],
                  'confidence':confidence,'authority':'unknown','assertedAuthority':c['authority'],
                  'materiality':{**c['materiality'],'priority':'high' if high else 'medium'},
                  'flags':flags,'uncertainty':c['uncertainty'],'policyDecision':policy}
        db.execute('INSERT INTO proposals VALUES(?,?,?,?)',(fingerprint,slot,fingerprint,encode(proposal).decode()))
        output.append((c,{'duplicate':fingerprint}))
    return output

def run_once(store,now=None,processor=curate):
    now=time.time() if now is None else now
    with store.connection() as db:
        ids=[r['id'] for r in db.execute("SELECT id FROM jobs WHERE status IN ('pending','retry') AND next_at<=? ORDER BY rowid LIMIT 100",(now,))]
    count=0
    for identity in ids:
        try:
            # One atomic transaction covers claim work and acknowledgement. A crash rolls back both.
            with store.transaction() as db:
                if not db.execute('SELECT enabled FROM control').fetchone()[0]:break
                job=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
                if job['status'] not in {'pending','retry'} or job['next_at']>now:continue
                source=db.execute('SELECT * FROM submissions WHERE id=?',(identity,)).fetchone()
                delta=json.loads(source['payload'])
                if digest(delta)!=source['hash']:raise ValueError('source-integrity')
                for claim,proposal in processor(delta,db,source['principal']):
                    db.execute('INSERT INTO occurrences VALUES(?,?,?)',(identity,claim['id'],proposal['duplicate'] if proposal else None))
                db.execute("UPDATE jobs SET status='done' WHERE id=?",(identity,))
                store.audit(db,'curated',identity);count+=1
        except Exception:
            with store.transaction() as db:
                job=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
                if job['status']=='done':continue
                attempts=job['attempts']+1
                db.execute('UPDATE jobs SET status=?,attempts=?,next_at=? WHERE id=?',('dead' if attempts>=3 else 'retry',attempts,now+min(3600,30*2**attempts),identity))
                store.audit(db,'curation-failed',identity)  # Exception text can contain source text; omit it.
    return {'processed':count,**store.health()}

def review(store,group_by='topic',expanded=False):
    if group_by not in {'topic','project','person','entity'}:raise ValueError('Invalid group')
    with store.connection() as db:
        items=[]
        for number,row in enumerate(db.execute('SELECT payload FROM proposals ORDER BY rowid'),1):
            p=json.loads(row['payload']);p['number']=number
            p['occurrences']=[dict(r) for r in db.execute('SELECT submission,claim FROM occurrences WHERE proposal=?',(p['id'],))]
            p['supportingEvidence']=[]
            for occurrence in p['occurrences']:
                delta=json.loads(db.execute('SELECT payload FROM submissions WHERE id=?',(occurrence['submission'],)).fetchone()[0])
                p['supportingEvidence'] += [{'sourceRef':c['sourceRef'],'evidence':c['evidence']} for c in delta['claims'] if c['subject']==p['claim']['subject'] and db.execute('SELECT proposal FROM occurrences WHERE submission=? AND claim=?',(occurrence['submission'],c['id'])).fetchone()[0] is None]
            items.append(p)
    groups={}
    for p in items:
        if not expanded and p['materiality']['priority']!='high':continue
        key=p['topic']
        if group_by in {'project','entity'}:key=p['subject']['label'] if group_by=='entity' or p['subject']['type']=='project' else 'Other: '+p['topic']
        if group_by=='person':key=p['claim']['owner'] or p['claim']['speaker']['identity'] or 'Unresolved person'
        groups.setdefault(key,[]).append(p)
    return {'groups':groups,'recommendedItems':sum(p['materiality']['priority']=='high' for p in items),'expandedItems':len(items),
            'snapshotHash':digest(items),'notice':'Private proposal preview only. No graph write or approval operation exists in capture.'}
