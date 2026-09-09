"""Frozen export-realism grading; no model invocation or graph mutation."""
import copy,hashlib,json,re
from datetime import datetime,timezone
from . import contract as c
from ..trial_v2.evaluate import score as basic_score,align

LOCK_HASH='692d28fca6d94563eac723f84a0a7995d13d86d90c84a3f110722f370b347e0e'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def normalized(s):return re.sub(r'^the ','',re.sub(r'[^a-z0-9]+',' ',s.casefold()).strip())
def measure(want,got):
    tp=len(want&got);fp=len(got-want);fn=len(want-got)
    return {'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None}

def grade(frozen,manifest_hash,out):
    mp=frozen/'frozen-manifest.json'
    if sha(mp)!=manifest_hash:raise ValueError('Manifest changed')
    manifest=json.loads(mp.read_text())
    if manifest['files']!={'output.json':sha(frozen/'output.json')}:raise ValueError('Output changed')
    receipt={'manifestHash':manifest_hash,'outputHash':sha(frozen/'output.json'),'gradingStartedAt':datetime.now(timezone.utc).isoformat()}
    with (frozen/'grading-receipt.json').open('x') as f:json.dump(receipt,f,indent=2)
    if sha(c.ROOT/'lock.json')!=LOCK_HASH:raise ValueError('Expected-result lock changed')
    lock=json.loads((c.ROOT/'lock.json').read_text())
    for name,h in lock['files'].items():
        if sha(c.ROOT.parents[1]/name.replace('\\','/'))!=h:raise ValueError('Locked artifact changed')
    gold=json.loads((c.ROOT/'expected.json').read_text())
    source=json.loads((c.ROOT/'synthetic-export.json').read_text())
    raw=json.loads((frozen/'output.json').read_text())
    errors=[]
    try:c.shape(raw,c.schema())
    except Exception as exc:errors.append('schema: '+str(exc))
    try:c.validate(source,raw)
    except Exception as exc:errors.append('validator: '+str(exc))
    # Structural errors remain a failed gate; do not repair output to obtain metrics.
    if any(x.startswith('schema:') for x in errors):
        result={'errors':errors,'gatePass':False,'receipt':receipt,'metrics':None}
    else:
        entity_map={}; entity_matches=set()
        for e in raw['entities']:
            names={normalized(e['label']),*[normalized(a) for a in e['aliases']]}
            possible=[g for g in gold['entities'] if g['type']==e['type'] and names&{normalized(g['label']),*[normalized(a) for a in g['aliases']]}]
            target=possible[0]['id'] if len(possible)==1 else 'unmatched-'+e['id']
            if target in entity_matches:target='split-'+e['id']
            entity_map[e['id']]=target;entity_matches.add(target)
        actual=copy.deepcopy(raw)
        for cl in actual['claims']:
            for k in ('speaker','subject','decisionOwner'):
                if cl[k] is not None:cl[k]=entity_map.get(cl[k],'invalid-'+cl[k])
            if cl['commitment'] and cl['commitment']['owner'] is not None:cl['commitment']['owner']=entity_map.get(cl['commitment']['owner'],'invalid-owner')
        for rel in actual['relationships']:
            for k in ('source','target'):rel[k]=entity_map.get(rel[k],'invalid-'+rel[k])
        assessment=basic_score(gold,actual);mapping=assessment['mapping'];metrics=assessment['metrics'];differences=assessment['differences']
        def extras(o,cm,em):
            d={'entities':{(em[e['id']],) for e in o['entities']},'duplicates':set(),'changes':set(),'recommendationStates':set()}
            for cl in o['claims']:
                for field,dim in [('duplicateOf','duplicates'),('supersedes','changes')]:
                    if cl[field] is not None:d[dim].add((cm[cl['id']],cm.get(cl[field],'invalid-target')))
                if cl['kind']=='recommendation':d['recommendationStates'].add((cm[cl['id']],cl['recommendationState']))
            return d
        wanted=extras(gold,{x['id']:x['id'] for x in gold['claims']},{x['id']:x['id'] for x in gold['entities']})
        got=extras(raw,mapping,entity_map)
        for dim,w in wanted.items():
            metrics[dim]=measure(w,got[dim]);differences[dim]={'extra':sorted(got[dim]-w,key=str),'missing':sorted(w-got[dim],key=str)}
        byconversation=[]
        for convo in source:
            occurrences=[x for x in raw['claims'] if x['conversationId']==convo['id']]
            byconversation.append({'id':convo['id'],'occurrences':len(occurrences),'nonduplicate':sum(x['duplicateOf'] is None for x in occurrences)})
        unique=sum(x['nonduplicate'] for x in byconversation)
        thresholds=all(m[k] is not None and m[k]>=.95 for m in metrics.values() for k in ('precision','recall'))
        avoidable=assessment['unnecessaryReviewCandidateCount']+metrics['duplicates']['fn']
        result={'run':'astra-realism-zero-api','apiCalls':0,'apiCostUsd':'0','errors':errors,'receipt':receipt,'lock':lock,
                'metrics':metrics,'differences':differences,'entityMap':entity_map,'claimMap':mapping,
                'unnecessaryReviewCandidateCount':assessment['unnecessaryReviewCandidateCount'],'missedDuplicateLinks':metrics['duplicates']['fn'],
                'avoidableReviewCandidateCount':avoidable,'byConversation':byconversation,
                'estimate100':{'nonduplicateClaimProposals':unique/len(source)*100,'entityCardsBeforeCrossBatchDedup':len(raw['entities'])/len(source)*100,
                    'rawClaimOccurrences':len(raw['claims'])/len(source)*100,'rangeClaimsPerConversation':[min(x['nonduplicate'] for x in byconversation),max(x['nonduplicate'] for x in byconversation)]},
                'gatePass':not errors and thresholds and avoidable==0,
                'notice':'Strict locked scoring, no repairs; false-claim and unnecessary-review candidates need source adjudication. Entity alias spelling is not a separate scored dimension.'}
    out.mkdir(exist_ok=True)
    (out/'Astra-realism-evaluation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    (out/'Astra-realism-frozen-output.json').write_bytes((frozen/'output.json').read_bytes())
    (out/'Astra-realism-freeze-manifest.json').write_bytes(mp.read_bytes())
    if sha(mp)!=manifest_hash or sha(frozen/'output.json')!=receipt['outputHash']:raise ValueError('Frozen artifacts changed during grading')
    return result
