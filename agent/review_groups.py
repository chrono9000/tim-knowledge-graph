"""Snapshot-based grouped manual review; no approval runs during planning."""
import hashlib,json
from pathlib import Path
from .ingest import atomic_json_write

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def plan(config,group_by='project',view='recommended'):
    from .intake import load_staging
    queue=load_staging(config.staging_path);groups={};seen={};all_ids=[p['id'] for b in queue['batches'] for p in b['proposals']]
    for b in queue['batches']:
        for p in b['proposals']:
            if p['status'] not in {'pending','needs-review'} or p['recordType']=='source':continue
            material=p.get('materiality',{'standalone':True,'priority':'medium','reason':'Legacy proposal; expanded review required.'})
            if not material['standalone'] or (view=='recommended' and material['priority']!='high'):continue
            keys=[k for k in p.get('groupKeys',[]) if k.startswith(group_by+':')]
            key=keys[0] if keys else 'topic:Unassigned'
            dedup=(key,p['recordType'],p.get('targetId'),p['record'].get('statementType'),p['record'].get('description'))
            if dedup in seen:
                seen[dedup]['duplicateProposalIds'].append(p['id']);continue
            item={'proposalId':p['id'],'number':all_ids.index(p['id'])+1,'duplicateProposalIds':[],
                'label':p['record'].get('label'),'description':p['record'].get('description'),
                'materiality':material,'supportingContext':p.get('supportingContext',[])}
            groups.setdefault(key,[]).append(item);seen[dedup]=item
    value={'version':1,'queueHash':fingerprint(queue),'groupBy':group_by,'view':view,'groups':groups,
           'notice':'Select an exact group for private approval only after reviewing every listed item. Planning makes no graph changes.'}
    value['planHash']=fingerprint(value)
    return value

def save_plan(config,path,group_by='project',view='recommended'):
    path=Path(path).resolve()
    if not path.is_relative_to(config.private_graph_path.parent.resolve()):raise ValueError('Review plans must remain inside the private data directory')
    value=plan(config,group_by,view);atomic_json_write(path,value);return value

def selected_ids(config,path,group):
    from .intake import load_staging
    path=Path(path).resolve()
    if not path.is_relative_to(config.private_graph_path.parent.resolve()):raise ValueError('Review plan must be private')
    value=json.loads(path.read_text(encoding='utf-8'));recorded=value.pop('planHash',None)
    if recorded!=fingerprint(value):raise ValueError('Review plan changed')
    if value['queueHash']!=fingerprint(load_staging(config.staging_path)):raise ValueError('Queue changed; create a fresh review plan')
    # Recompute membership, not merely a user-editable plan hash.
    expected=plan(config,value['groupBy'],value['view'])
    if value['groups']!=expected['groups'] or group not in value['groups']:raise ValueError('Invalid review group')
    return [p for x in value['groups'][group] for p in [x['proposalId'],*x['duplicateProposalIds']]]
