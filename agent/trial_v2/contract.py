"""Typed evidence-backed trial proposals; independent from production approval."""
import json
import re
from pathlib import Path
from dataclasses import dataclass
from ..extraction import Claim

ROOT=Path(__file__).parent
KINDS=['statement','recommendation','preference','proposed-choice','decision','commitment','constraint','unresolved-question']
FLAG_FIELDS=['speaker','subject','decisionOwner','commitment.owner','commitment.due','acceptance','conflict']
FLAG_CODES=['ambiguous-reference','missing-owner','ambiguous-date','ambiguous-time','missing-timezone','ambiguous-acceptance','possible-conflict']

def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}

def schema():
    string={'type':'string'}
    nullable={'type':['string','null']}
    span=obj({'messageId':string,'quote':string})
    due=obj({'date':nullable,'time':nullable,'timezone':nullable,
             'ambiguity':{'type':'string','enum':['none','date','time','timezone']}})
    commitment=obj({'owner':nullable,'due':due})
    commitment['type']=['object','null']
    amount=obj({'value':{'type':'number'},'unit':string,'operator':{'type':'string','enum':['<=','>=','=']},'qualifier':nullable})
    amount['type']=['object','null']
    claim=obj({'id':string,'messageId':string,'quote':string,'kind':{'type':'string','enum':KINDS},
               'speaker':nullable,'subject':nullable,'decisionOwner':nullable,
               'commitment':commitment,'amount':amount,'support':{'type':'array','items':span}})
    relation=obj({'type':{'type':'string','enum':['owner-of','depends-on']},
                  'source':string,'target':string,'evidenceClaim':string})
    flag=obj({'code':{'type':'string','enum':FLAG_CODES},'field':{'type':'string','enum':FLAG_FIELDS},
              'claimIds':{'type':'array','items':string},'reason':string})
    return obj({'claims':{'type':'array','items':claim},'relationships':{'type':'array','items':relation},
                'flags':{'type':'array','items':flag}})

def shape(value,spec,path='$'):
    types=spec['type'] if isinstance(spec['type'],list) else [spec['type']]
    import math
    good={'null':value is None,'object':type(value) is dict,'array':type(value) is list,
          'string':type(value) is str,'number':type(value) in (int,float) and math.isfinite(value) if type(value) in (int,float) else False}
    if not any(good[t] for t in types): raise ValueError(path+': invalid type')
    if 'enum' in spec and value not in spec['enum']: raise ValueError(path+': invalid enum')
    if type(value) is dict:
        if set(value)!=set(spec['properties']): raise ValueError(path+': wrong fields')
        for k,v in value.items(): shape(v,spec['properties'][k],path+'.'+k)
    if type(value) is list:
        for i,v in enumerate(value): shape(v,spec['items'],path+f'[{i}]')

def validate(case,output):
    shape(output,schema())
    entities={e['id']:e for e in case['entities']}
    messages={m['id']:m for m in case['messages']}
    claims={c['id']:c for c in output['claims']}
    if len(claims)!=len(output['claims']) or len(claims)>64: raise ValueError('Duplicate/too many claims')
    def entity(ref,types=None):
        if ref is None: return
        if ref not in entities or (types and entities[ref]['type'] not in types): raise ValueError('Invalid entity reference')
    def evidenced_owner(ref,c,decision=False):
        entity(ref,{'person'})
        msg=messages[c['messageId']]
        if not ref: raise ValueError('Owner missing')
        spans=' '.join(s['quote'] for s in c['support'])
        verbs = r'(?:approve\w*|authoriz\w*|decid\w*|select\w*|choose|have chosen)' if decision else r'(?:will|accept responsibility|undertake|have undertaken)'
        explicit_first = msg.get('speaker')==ref and re.search(r'\bI\s+'+verbs+r'\b',c['quote'],re.I)
        explicit_named = re.search(r'\b'+re.escape(entities[ref]['label'])+r'\s+'+verbs+r'\b',c['quote'],re.I)
        contextual_acceptance = msg.get('speaker')==ref and c['quote'].strip().lower() in {'approved.','approved','yes.','yes','go ahead.','go ahead'}
        if not (explicit_first or explicit_named or contextual_acceptance): raise ValueError('Owner lacks explicit evidence')
        if decision and not re.search(r'\b(approve\w*|authoriz\w*|decid\w*|select\w*|choose|chosen|go ahead)\b',spans,re.I):
            raise ValueError('Decision lacks explicit adoption evidence')
        if contextual_acceptance:
            ids=list(messages)
            if not any(ids.index(s['messageId'])<ids.index(c['messageId']) for s in c['support']): raise ValueError('Acceptance lacks preceding context')
    for c in claims.values():
        if c['messageId'] not in messages or not 1<=len(c['quote'])<=500 or c['quote'] not in messages[c['messageId']]['text']: raise ValueError('Invalid primary evidence')
        if c['speaker']!=messages[c['messageId']].get('speaker'): raise ValueError('Speaker differs from source metadata')
        entity(c['speaker'],{'person'}); entity(c['subject'])
        if not c['support']: raise ValueError('Support required')
        for s in c['support']:
            if s['messageId'] not in messages or not 1<=len(s['quote'])<=500 or s['quote'] not in messages[s['messageId']]['text']: raise ValueError('Invalid supporting evidence')
        if c['kind']=='decision':
            if messages[c['messageId']]['role']!='user': raise ValueError('Assistant cannot adopt user decision')
            evidenced_owner(c['decisionOwner'],c,True)
        elif c['decisionOwner'] is not None: raise ValueError('Non-decision cannot have decision owner')
        if c['kind']=='commitment':
            if c['commitment'] is None: raise ValueError('Commitment fields missing')
            owner=c['commitment']['owner']
            if owner is not None: evidenced_owner(owner,c)
            elif not any(f['field']=='commitment.owner' and c['id'] in f['claimIds'] for f in output['flags']): raise ValueError('Missing commitment owner needs material flag')
            due=c['commitment']['due']
            if due['timezone']!=case['timezone']: raise ValueError('Source timezone differs')
            if due['date'] is not None:
                from datetime import date
                date.fromisoformat(due['date'])
            if due['time'] is not None and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',due['time']): raise ValueError('Invalid time')
            if due['ambiguity'] in {'date','timezone'} and (due['date'] is not None or due['time'] is not None): raise ValueError('Ambiguous date must stay null')
            if due['ambiguity']=='time' and due['time'] is not None: raise ValueError('Ambiguous time must stay null')
            needed={'date':'ambiguous-date','time':'ambiguous-time','timezone':'missing-timezone'}.get(due['ambiguity'])
            if needed and not any(f['code']==needed and f['field']=='commitment.due' and f['claimIds']==[c['id']] for f in output['flags']): raise ValueError('Due ambiguity needs matching flag')
        elif c['commitment'] is not None: raise ValueError('Non-commitment has commitment fields')
        if c['amount'] is not None and c['kind']!='constraint': raise ValueError('Amount must belong to constraint')
    relations=set()
    for rel in output['relationships']:
        if rel['evidenceClaim'] not in claims: raise ValueError('Relationship lacks claim evidence')
        a,b=rel['source'],rel['target']
        entity(a); entity(b)
        if a==b or a is None or b is None: raise ValueError('Invalid relationship target')
        if rel['type']=='owner-of':
            entity(a,{'person'}); entity(b,{'project','system','asset'})
        else:
            entity(a,{'project','system'}); entity(b,{'project','system'})
        key=(rel['type'],a,b)
        if key in relations: raise ValueError('Redundant relationship')
        relations.add(key)
        quote=claims[rel['evidenceClaim']]['quote'].casefold()
        if not all(entities[x]['label'].casefold() in quote for x in (a,b)): raise ValueError('Relationship endpoints lack explicit evidence')
        if rel['type']=='owner-of' and not re.search(r'\b(owns|responsible for|owner of)\b',quote): raise ValueError('Ownership not explicit')
        if rel['type']=='depends-on' and 'depends on' not in quote: raise ValueError('Dependency not explicit')
    seen=set()
    for f in output['flags']:
        if not 10<=len(f['reason'])<=300 or not f['claimIds'] or any(x not in claims for x in f['claimIds']): raise ValueError('Flag lacks specific explanation/target')
        key=(f['code'],f['field'],tuple(sorted(f['claimIds'])))
        if key in seen: raise ValueError('Redundant flag')
        seen.add(key)
        fields={'ambiguous-date':{'commitment.due'},'ambiguous-time':{'commitment.due'},'missing-timezone':{'commitment.due'},'missing-owner':{'decisionOwner','commitment.owner'},'ambiguous-acceptance':{'acceptance'},'ambiguous-reference':{'speaker','subject','commitment.owner'},'possible-conflict':{'conflict'}}
        if f['field'] not in fields[f['code']]: raise ValueError('Flag code/field mismatch')
        if f['code']!='possible-conflict' and len(f['claimIds'])!=1: raise ValueError('Field ambiguity needs a single claim')
        if f['field']=='decisionOwner' and claims[f['claimIds'][0]]['decisionOwner'] is not None: raise ValueError('Owner is not missing')
        if f['field'].startswith('commitment.') and claims[f['claimIds'][0]]['kind']!='commitment': raise ValueError('Flag has invalid commitment target')
        if f['code']=='possible-conflict':
            if f['field']!='conflict' or len(set(f['claimIds']))!=2: raise ValueError('Conflict must identify a claim pair')
            pair=[claims[x] for x in f['claimIds']]
            if any(c['kind']!='preference' for c in pair) or pair[0]['speaker'] is None or pair[0]['speaker']!=pair[1]['speaker'] or pair[0]['subject']!=pair[1]['subject']: raise ValueError('Conflict scope/person mismatch')
    return output

@dataclass(frozen=True)
class ContextClaim(Claim):
    context: dict=None

class RecordedExtractor:
    """Same Extractor.extract(messages) signature; no live provider."""
    name,version='astra-codex-blind','2'
    def __init__(self,case,output): self.case,self.output=case,output
    def extract(self,messages):
        if [(m.id,m.text) for m in messages]!=[(m['id'],m['text']) for m in self.case['messages']]: raise ValueError('Source mismatch')
        validate(self.case,self.output)
        epistemic={'statement':'user-statement','proposed-choice':'assumption','commitment':'user-statement','constraint':'user-statement'}
        return [ContextClaim(c['messageId'],c['quote'],c['kind'],epistemic.get(c['kind'],c['kind']),.5,c['decisionOwner'],False,c) for c in self.output['claims']]
