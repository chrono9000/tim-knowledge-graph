"""Provider-independent bounded delta contract. Evidence is untrusted input."""
import hashlib
import json
import re
from datetime import datetime
from ..trial_v2.contract import obj, shape
from ..materiality import CRITERIA

MAX_BYTES = 32768
KINDS = ['fact','decision','change','preference','constraint','commitment','ownership',
         'risk','unresolved-question','entity','project','system','relationship','recommendation','context']

def schema():
    s={'type':'string'}; n={'type':['string','null']}
    entity=obj({'id':s,'label':s,'type':{'type':'string','enum':['person','entity','project','system','topic']}})
    due=obj({'date':n,'time':n,'timezone':n,'ambiguity':{'type':'string','enum':['none','date','time','timezone']}})
    relation=obj({'type':{'type':'string','enum':['owner-of','depends-on']},'source':s,'target':s})
    relation['type']=['object','null']
    claim=obj({'id':s,'type':{'type':'string','enum':KINDS},'subject':s,'scope':s,'predicate':s,'value':s,
        'speaker':obj({'role':{'type':'string','enum':['user','assistant','unknown']},'identity':n}),
        'confidence':{'type':'number'},'authority':{'type':'string','enum':['owner','primary','secondary','tertiary','unknown']},
        'materiality':obj({'criterion':{'type':'string','enum':CRITERIA},'durability':{'type':'string','enum':['durable','temporary']},'reason':s}),
        'owner':n,'due':due,'supersedes':n,'relationship':relation,
        'sourceRef':s,'evidence':s,
        'uncertainty':{'type':'array','items':obj({'field':{'type':'string','enum':['speaker','subject','owner','due','acceptance']},'reason':s})}})
    return obj({'version':{'type':'number','enum':[1]},'conversationId':s,'topic':s,'sourceTimestamp':s,
                'entities':{'type':'array','items':entity},'claims':{'type':'array','items':claim}})

def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')

def digest(value):return hashlib.sha256(encode(value)).hexdigest()

def strict_json(raw):
    if len(raw)>MAX_BYTES:raise ValueError('oversized')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate-json-key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))

def validate(delta):
    shape(delta,schema())
    if len(encode(delta))>MAX_BYTES:raise ValueError('oversized')
    stamp=datetime.fromisoformat(delta['sourceTimestamp'].replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('source timezone required')
    if not 1<=len(delta['claims'])<=20 or not 1<=len(delta['entities'])<=30:raise ValueError('item count')
    def strings(value):
        if isinstance(value,str) and (len(value)>600 or not value.strip()):raise ValueError('string bounds')
        if isinstance(value,dict):
            for v in value.values():strings(v)
        if isinstance(value,list):
            for v in value:strings(v)
    strings(delta)
    entities={e['id']:e for e in delta['entities']}
    if len(entities)!=len(delta['entities']):raise ValueError('duplicate entities')
    if len({c['id'] for c in delta['claims']})!=len(delta['claims']):raise ValueError('duplicate claims')
    for c in delta['claims']:
        if c['subject'] not in entities or not 0<=c['confidence']<=1:raise ValueError('subject or confidence')
        for ref in [c['owner'],c['speaker']['identity']]:
            if ref is not None and (ref not in entities or entities[ref]['type']!='person'):raise ValueError('person reference')
        if c['type']=='decision' and (not c['owner'] or c['speaker']['role']!='user'):raise ValueError('decision owner/adoption evidence required')
        if c['type'] in {'decision','change','commitment','ownership','risk'} and c['materiality']['criterion']=='context':raise ValueError('material claim cannot be hidden')
        if len(c['evidence'])>400 or len(c['materiality']['reason'])<15:raise ValueError('evidence or materiality bounds')
        if len(c['uncertainty'])>3 or any(len(f['reason'])<15 for f in c['uncertainty']):raise ValueError('specific uncertainty required')
        if c['type']=='commitment' and c['owner'] is None and not any(f['field']=='owner' for f in c['uncertainty']):raise ValueError('missing commitment owner flag')
        due=c['due']
        if due['date']:
            datetime.strptime(due['date'],'%Y-%m-%d')
            if not due['timezone']:raise ValueError('deadline timezone required')
        if due['time'] and (not due['date'] or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',due['time'])):raise ValueError('deadline time')
        if due['ambiguity']!='none' and not any(f['field']=='due' for f in c['uncertainty']):raise ValueError('deadline ambiguity flag required')
        if c['type']=='change' and not c['supersedes']:raise ValueError('change target required')
        if (c['type']=='relationship')!=(c['relationship'] is not None):raise ValueError('relationship classification')
        if c['relationship']:
            r=c['relationship']
            if r['source'] not in entities or r['target'] not in entities or r['source']==r['target']:raise ValueError('relationship target')
            if r['type']=='owner-of' and (entities[r['source']]['type']!='person' or entities[r['target']]['type'] not in {'entity','project','system'}):raise ValueError('ownership endpoints')
            if r['type']=='depends-on' and any(entities[r[x]]['type'] not in {'entity','project','system'} for x in ['source','target']):raise ValueError('dependency endpoints')
    return delta

def idempotency_key(delta):
    return digest({'conversation':delta['conversationId'],'claims':sorted(c['id'] for c in delta['claims'])})

TOOL_NAME='capture_knowledge_delta'
def tool_descriptor():
    return {'name':TOOL_NAME,'description':'Submit only supported durable knowledge and bounded evidence from this conversation for later PRIVATE HUMAN REVIEW. Never approve or publish.',
            'inputSchema':schema(),'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False},
            'securitySchemes':[{'type':'oauth2','scopes':['capture:write']}]}
