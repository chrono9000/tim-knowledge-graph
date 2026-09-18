"""Local human review bridge. No network, publication or FEOS document writes."""
import copy
import json
from .contract import digest
from .curator import review
from .secure_sync import private_path
from ..intake import load_staging,load_private_master,_proposal,approve,reject,_write_log
from ..harness import load_harness
from ..ingest import atomic_json_write,iso_timestamp
from ..safety import transactional,locked,state_dir

def paths(config):
    for p in [config.private_graph_path,config.staging_path,config.log_dir]:private_path(p)
    if (state_dir(config)/'STOP').exists():raise ValueError('Workflow paused')

@transactional
def stage(store,config):
    paths(config);store.verify_history()
    if not store.health()['enabled']:raise ValueError('Capture paused')
    staging=load_staging(config.staging_path);graph=load_private_master(config)
    harness=load_harness(config.harness_path)
    items=[p for group in review(store,expanded=True)['groups'].values() for p in group]
    added=[]
    for p in items:
        batch_id='capture-'+p['id']
        if any(b['id']==batch_id for b in staging['batches']):continue
        stamp=p['sourceTimestamp'];source_id='source-'+p['id'];c=p['claim']
        source={'id':source_id,'title':p['topic'],'type':'conversation','location':'private-capture:'+p['id'],'filename':'structured-capture','sourceTimestamp':stamp,'contentHash':digest(p),'retrievedAt':iso_timestamp(config.clock()),'confidence':p['confidence'],'authorityLevel':p['authority']}
        proposals=[_proposal(batch_id,'source','add',source,source,harness)]
        common={'confidence':p['confidence'],'authorityLevel':p['authority'],'sourceIds':[source_id],'timestamps':{'createdAt':stamp,'updatedAt':stamp,'firstSeen':stamp,'lastSeen':stamp}}
        category=graph['categories'][0]['id']
        if c['relationship']:
            with store.connection() as db:
                delta=json.loads(db.execute('SELECT payload FROM submissions WHERE id=?',(p['occurrences'][0]['submission'],)).fetchone()[0])
            entities={e['id']:e for e in delta['entities']};r=c['relationship'];ends={}
            for ref in [r['source'],r['target']]:
                e=entities[ref];ident='entity-'+digest([p['conversationId'],ref])[:24];ends[ref]=ident
                record={**common,'id':ident,'label':e['label'],'description':'Entity referenced by reviewed relationship.','entityType':e['type'],'statementType':'fact','category':category}
                proposals.append(_proposal(batch_id,'node','add',record,source,harness))
            record={**common,'id':'relation-'+p['id'],'source':ends[r['source']],'target':ends[r['target']],'relationship':r['type'],'statementType':'fact','directed':True}
            proposals.append(_proposal(batch_id,'edge','add',record,source,harness))
        else:
            mapping={'change':'fact','constraint':'policy','commitment':'fact','ownership':'fact','risk':'unresolved-question','unresolved-question':'unresolved-question'}
            record={**common,'id':'capture-'+p['id'],'label':p['subject']['label']+': '+c['predicate'],'description':c['value'],'entityType':p['subject']['type'] if p['subject']['type']!='topic' else 'entity','statementType':mapping.get(c['type'],c['type'] if c['type'] in harness['statementTypes'] else 'fact'),'category':category}
            previous=None
            if c['supersedes'] and any(f['code']=='possible-supersession' for f in p['flags']):
                prior_batch=next((b for b in staging['batches'] if b.get('captureId')==c['supersedes']),None)
                prior_id=next((q['targetId'] for q in prior_batch['proposals'] if q['recordType']=='node'),None) if prior_batch else None
                previous=next((n for n in graph['nodes'] if n['id']==prior_id),None)
            if previous:
                proposed=copy.deepcopy(record);proposed['id']=previous['id']
                record={**previous,**common,'description':previous['description']}
                proposals.append(_proposal(batch_id,'node','update',record,source,harness,previous,proposed))
            else:
                # Unresolved changes remain independent proposals; no implicit replacement.
                proposals.append(_proposal(batch_id,'node','add',record,source,harness))
        for q in proposals:
            q['policyDecision']['publicEligible']=False
            q['captureMetadata']=copy.deepcopy(p)
            q['reviewReasons']=sorted(set(q['reviewReasons']+[f['code'] for f in p['flags']]))
        staging['batches'].append({'id':batch_id,'private':True,'source':source,'sourceTimestamp':stamp,'sourceFilename':'structured-capture','proposals':proposals,'captureId':p['id'],'topic':p['topic'],'captureMetadata':p})
        added.append(batch_id)
    atomic_json_write(config.staging_path,staging)
    _write_log(config,'capture-stage',{'batchIds':added})
    return added

def snapshot(config):
    paths(config)
    with locked(config):
        staging=load_staging(config.staging_path);graph=load_private_master(config)
        groups={}
        for b in staging['batches']:
            if 'captureId' not in b:continue
            pending=[p for p in b['proposals'] if p['status'] in {'pending','needs-review'}]
            if pending:groups.setdefault(b['topic'],[]).append({'captureId':b['captureId'],'proposalIds':[p['id'] for p in pending],'records':[p['record'] for p in pending],'changes':[{'previous':p.get('previousRecord'),'proposed':p.get('proposedRecord')} for p in pending if p.get('proposedRecord')],'metadata':b['captureMetadata']})
        return {'hash':digest([staging,graph]),'groups':groups,'notice':'Selecting a capture item includes the explicitly displayed source and relationship endpoints. Only private approval is available.'}

@transactional
def decide(config,expected_hash,capture_ids,decision,reviewer):
    paths(config)
    if decision not in {'approve-private','reject'} or not reviewer.strip():raise ValueError('Explicit private decision and reviewer required')
    if not capture_ids or len(set(capture_ids))!=len(capture_ids):raise ValueError('Select unique capture IDs')
    current=snapshot(config)
    if current['hash']!=expected_hash:raise ValueError('Stale review; refresh')
    items={p['captureId']:p for g in current['groups'].values() for p in g}
    if any(x not in items for x in capture_ids):raise ValueError('Unknown selection')
    ids=[i for x in capture_ids for i in items[x]['proposalIds']]
    result=approve(config,'private',proposal_ids=ids) if decision=='approve-private' else reject(config,proposal_ids=ids)
    _write_log(config,'capture-human-decision',{'reviewer':reviewer,'snapshot':expected_hash,'captureIds':capture_ids,'decision':decision})
    return result
