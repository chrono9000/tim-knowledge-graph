"""Frozen-output evaluation only; never extracts, calls a model, or approves."""
import hashlib,json
from datetime import datetime,timezone
from . import contract as c
from ..harness import load_harness,evaluate_proposal

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def locked_suite():
    lock=json.loads((c.ROOT/'holdout-lock.json').read_text())
    repo=c.ROOT.parents[1]
    if sha(c.ROOT/'holdout-lock.json')!='c8cb07c3d8fe7dc5e28662060473548104152741a7f985e28212f8569141fe6b': raise ValueError('Holdout lock changed')
    for name,digest in lock['files'].items():
        if sha(repo/name.replace('\\','/'))!=digest: raise ValueError('Locked artifact changed: '+name)
    return json.loads((c.ROOT/'holdout-inputs.json').read_text()),json.loads((c.ROOT/'holdout-expected.json').read_text()),lock

def align(gold,actual):
    mapping={}; used=set()
    for i,p in enumerate(actual['claims']):
        match=next((g for g in gold['claims'] if g['id'] not in used and g['messageId']==p['messageId'] and
                    (g['quote'] in p['quote'] or p['quote'] in g['quote'])),None)
        mapping[p['id']]=match['id'] if match else f'extra-{i}'
        if match: used.add(match['id'])
    return mapping

def tuples(output,mapping):
    sets={k:set() for k in ['claims','classifications','speakers','subjects','decisionOwnership','commitments','deadlines','relationships','conflicts','uncertaintyFlags']}
    for claim in output['claims']:
        key=mapping[claim['id']]
        sets['claims'].add((key,))
        sets['classifications'].add((key,claim['kind']))
        for field,dimension in [('speaker','speakers'),('subject','subjects'),('decisionOwner','decisionOwnership')]:
            if claim[field] is not None: sets[dimension].add((key,claim[field]))
        if claim['commitment'] is not None:
            sets['commitments'].add((key,claim['commitment']['owner']))
            for field,value in claim['commitment']['due'].items():
                if value is not None and not (field=='ambiguity' and value=='none'):
                    sets['deadlines'].add((key,field,value))
    for rel in output['relationships']:
        sets['relationships'].add((rel['type'],rel['source'],rel['target']))
    for f in output['flags']:
        refs=tuple(sorted(mapping.get(x,'invalid-'+x) for x in f['claimIds']))
        if f['code']=='possible-conflict': sets['conflicts'].add(refs)
        else: sets['uncertaintyFlags'].add((f['code'],f['field'],refs))
    return sets

def score(gold,actual):
    mapping=align(gold,actual)
    wanted=tuples(gold,{x['id']:x['id'] for x in gold['claims']}); got=tuples(actual,mapping)
    metrics={}; diffs={}
    for name,w in wanted.items():
        a=got[name]; tp=len(w&a); fp=len(a-w); fn=len(w-a)
        metrics[name]={'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None}
        diffs[name]={'extra':sorted(a-w,key=str),'missing':sorted(w-a,key=str)}
    # Strict candidates for unnecessary review, adjudicated without changing scores.
    extra_flags=got['uncertaintyFlags']-wanted['uncertaintyFlags']
    extra_conflicts=got['conflicts']-wanted['conflicts']
    burdens={key for _,_,refs in extra_flags for key in refs}
    burdens.update(key for refs in extra_conflicts for key in refs)
    burdens.update(key for key, in got['claims']-wanted['claims'])
    return {'metrics':metrics,'differences':diffs,'mapping':mapping,
            'unnecessaryReviewCandidateClaims':sorted(burdens),'unnecessaryReviewCandidateCount':len(burdens)}

def evaluate(frozen,manifest_hash,out):
    mp=frozen/'frozen-manifest.json'
    if sha(mp)!=manifest_hash: raise ValueError('Manifest hash differs from sub-agent attestation')
    manifest=json.loads(mp.read_text())
    observed={f'{i:02}.json':sha(frozen/f'{i:02}.json') for i in range(1,15)}
    if manifest['files']!=observed: raise ValueError('Frozen output changed')
    receipt={'manifestHash':manifest_hash,'files':observed,'gradingStartedAt':datetime.now(timezone.utc).isoformat()}
    with (frozen/'grading-receipt.json').open('x') as f: json.dump(receipt,f,indent=2)
    cases,gold,lock=locked_suite()
    rows=[]; totals={}; proposals=[]; unnecessary=0
    for i,(case,g) in enumerate(zip(cases,gold),1):
        raw=json.loads((frozen/f'{i:02}.json').read_text())
        errors=[]
        try: c.shape(raw,c.schema())
        except Exception as exc: errors.append('schema: '+str(exc))
        try: c.validate(case,raw)
        except Exception as exc: errors.append('validator: '+str(exc))
        assessment=score(g,raw) if not any(x.startswith('schema:') for x in errors) else None
        rows.append({'caseNumber':i,'errors':errors,'assessment':assessment})
        if assessment:
            unnecessary+=assessment['unnecessaryReviewCandidateCount']
            for dim,metric in assessment['metrics'].items():
                t=totals.setdefault(dim,{'tp':0,'fp':0,'fn':0})
                for k in t: t[k]+=metric[k]
        for claim in raw.get('claims',[]):
            flags=[f for f in raw.get('flags',[]) if claim['id'] in f['claimIds']]
            proposals.append({'number':len(proposals)+1,'caseNumber':i,'status':'needs-review' if not errors else 'validation-blocked',
                 'approvalEligible':False,'classification':'private','confidence':.5,'authority':'unknown',
                 'claim':claim,'flags':flags,'relationships':[r for r in raw.get('relationships',[]) if r['evidenceClaim']==claim['id']],
                 'validationErrors':errors,'harnessRuleIds':[r['id'] for r in load_harness()['rules']]})
    for m in totals.values():
        m['precision']=m['tp']/(m['tp']+m['fp']) if m['tp']+m['fp'] else None
        m['recall']=m['tp']/(m['tp']+m['fn']) if m['tp']+m['fn'] else None
    passes=all(not r['errors'] for r in rows) and unnecessary==0 and all(m[k] is not None and m[k]>=.95 for m in totals.values() for k in ('precision','recall'))
    result={'run':'astra-codex-v2-blind','apiCalls':0,'apiCostUsd':'0','receipt':receipt,'lock':lock,'totals':totals,
            'cases':rows,'unnecessaryReviewCandidateCount':unnecessary,'predeclaredGatePass':passes,
            'notice':'Raw scores and review candidates; no output or gold changes after freeze. Codex usage is not metered here.'}
    out.mkdir(exist_ok=True)
    for name,value in [('Astra-v2-evaluation.json',result),('Astra-v2-review-proposals.json',proposals),
                       ('Astra-v2-frozen-output.json',{'manifest':manifest,'responses':[json.loads((frozen/f'{i:02}.json').read_text()) for i in range(1,15)]})]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    if observed!={name:sha(frozen/name) for name in observed} or sha(mp)!=manifest_hash: raise ValueError('Frozen content changed during grading')
    return result
