import copy,json,re
from pathlib import Path
from ..trial_v2.contract import schema as v2schema,shape,obj

ROOT=Path(__file__).parent

def schema():
    s=copy.deepcopy(v2schema()); string={'type':'string'}; nullable={'type':['string','null']}
    span=obj({'conversationId':string,'messageId':string,'quote':string})
    claim=s['properties']['claims']['items']
    claim['properties'].update({'conversationId':string,'duplicateOf':nullable,'supersedes':nullable,
                                'recommendationState':{'type':'string','enum':['not-applicable','accepted','rejected','unanswered']}})
    claim['properties']['support']={'type':'array','items':span}; claim['required']=list(claim['properties'])
    entity=obj({'id':string,'label':string,'type':{'type':'string','enum':['person','project','system','asset','topic']},
                'aliases':{'type':'array','items':string},'support':{'type':'array','items':span}})
    s['properties']['entities']={'type':'array','items':entity}; s['required']=list(s['properties'])
    return s

def messages(export):
    result={}
    for conversation in export:
        mapping=conversation['mapping']; cur=conversation['current_node']; chain=[]; seen=set()
        while cur is not None:
            if cur in seen or cur not in mapping: raise ValueError('Invalid export ancestry')
            seen.add(cur); node=mapping[cur]
            if node.get('message') is not None: chain.append(node['message'])
            cur=node.get('parent')
        for m in reversed(chain):
            text='\n'.join(p for p in m['content']['parts'] if isinstance(p,str))
            result[(conversation['id'],m['id'])]={'role':m['author']['role'],'text':text,'timestamp':m.get('create_time')}
    return result

def validate(export,output):
    shape(output,schema()); src=messages(export)
    es={e['id']:e for e in output['entities']}; cs={c['id']:c for c in output['claims']}
    if len(es)!=len(output['entities']) or len(cs)!=len(output['claims']): raise ValueError('Duplicate IDs')
    def evidence(span):
        key=(span['conversationId'],span['messageId'])
        if key not in src or not span['quote'] or span['quote'] not in src[key]['text']: raise ValueError('Unsupported evidence span')
    for e in es.values():
        if not e['support']: raise ValueError('Entity lacks evidence')
        for span in e['support']: evidence(span)
        if not any(e['label'].casefold() in x['quote'].casefold() for x in e['support']): raise ValueError('Canonical entity label lacks source evidence')
        for alias in e['aliases']:
            if not any(alias.casefold() in x['quote'].casefold() for x in e['support']): raise ValueError('Alias lacks evidence')
    def ref(value,types=None):
        if value is not None and (value not in es or (types and es[value]['type'] not in types)): raise ValueError('Invalid entity reference')
    for c in cs.values():
        evidence(c)
        for span in c['support']: evidence(span)
        if not c['support']: raise ValueError('Claim lacks support')
        for k in ('speaker','decisionOwner'): ref(c[k],{'person'})
        ref(c['subject'])
        if re.search(r'\bI (?:can|could|am happy to|would be happy to) (?:help|assist)\b',c['quote'],re.I) and c['kind']!='recommendation': raise ValueError('An offer to help is a recommendation')
        if (c['kind']=='recommendation') != (c['recommendationState']!='not-applicable'): raise ValueError('Recommendation disposition mismatch')
        if c['kind']=='decision':
            if src[(c['conversationId'],c['messageId'])]['role']!='user' or c['decisionOwner'] is None: raise ValueError('Decision lacks user evidence and owner')
            words=' '.join(x['quote'] for x in c['support'])
            if not re.search(r'\b(approve\w*|authoriz\w*|decid\w*|choose|selected|go ahead)\b',words,re.I): raise ValueError('Decision lacks adoption evidence')
        elif c['decisionOwner'] is not None: raise ValueError('Unexpected decision owner')
        if c['kind']=='commitment':
            if c['commitment'] is None: raise ValueError('Commitment missing structure')
            ref(c['commitment']['owner'],{'person'})
            d=c['commitment']['due']
            if d['date'] is not None:
                from datetime import date
                date.fromisoformat(d['date'])
            if d['time'] is not None and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',d['time']): raise ValueError('Invalid time')
            if d['ambiguity'] in {'date','timezone'} and (d['date'] is not None or d['time'] is not None): raise ValueError('Ambiguous deadline cannot be definite')
            if d['ambiguity']=='time' and d['time'] is not None: raise ValueError('Ambiguous time must stay null')
            code={'date':'ambiguous-date','time':'ambiguous-time','timezone':'missing-timezone'}.get(d['ambiguity'])
            if code and not any(f['code']==code and f['field']=='commitment.due' and c['id'] in f['claimIds'] for f in output['flags']): raise ValueError('Deadline ambiguity lacks flag')
        elif c['commitment'] is not None: raise ValueError('Noncommitment has commitment fields')
        for field in ('duplicateOf','supersedes'):
            target=c[field]
            if target is not None:
                if target not in cs or target==c['id'] or list(cs).index(target)>=list(cs).index(c['id']): raise ValueError('Invalid history reference')
                if cs[target]['subject']!=c['subject']: raise ValueError('History crosses subjects')
        if c['duplicateOf'] and c['supersedes']: raise ValueError('Duplicate cannot also supersede')
    edges=set()
    for r in output['relationships']:
        ref(r['source']);ref(r['target'])
        if r['source'] is None or r['target'] is None or r['source']==r['target'] or r['evidenceClaim'] not in cs: raise ValueError('Invalid relationship endpoints')
        if r['type']=='owner-of': ref(r['source'],{'person'});ref(r['target'],{'project','system','asset'})
        else: ref(r['source'],{'project','system'});ref(r['target'],{'project','system'})
        key=(r['type'],r['source'],r['target'])
        if key in edges: raise ValueError('Redundant edge')
        edges.add(key)
    flags=set()
    for f in output['flags']:
        if len(f['reason'])<10 or not f['claimIds'] or any(x not in cs for x in f['claimIds']): raise ValueError('Invalid material flag')
        key=(f['code'],f['field'],tuple(sorted(f['claimIds'])))
        if key in flags: raise ValueError('Duplicate flag')
        flags.add(key)
        if f['code']=='possible-conflict':
            if f['field']!='conflict' or len(set(f['claimIds']))!=2: raise ValueError('Invalid conflict pair')
            a,b=[cs[x] for x in f['claimIds']]
            if a['speaker'] is None or a['speaker']!=b['speaker'] or a['subject']!=b['subject']: raise ValueError('Conflict crosses owner or scope')
    return output
