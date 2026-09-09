import copy,json,hashlib
from pathlib import Path
from ..trial_v3 import contract as prior
from ..trial_v2.contract import obj,shape
from ..materiality import CRITERIA

ROOT=Path(__file__).parent
def schema():
    s=copy.deepcopy(prior.schema());cl=s['properties']['claims']['items']
    cl['properties']['kind']['enum'] += ['risk','status']
    # Use a string disposition instead of a boolean to keep validation explicit.
    cl['properties']['materiality']=obj({'route':{'type':'string','enum':['proposal','evidence']},
        'criterion':{'type':'string','enum':CRITERIA},'durability':{'type':'string','enum':['durable','temporary']},
        'priority':{'type':'string','enum':['high','medium','low']},'reason':{'type':'string'}})
    cl['properties']['supportingFor']={'type':'array','items':{'type':'string'}}
    cl['required']=list(cl['properties']);return s

def validate(source,output):
    shape(output,schema())
    clean=copy.deepcopy(output)
    for cl in clean['claims']:
        del cl['materiality'];del cl['supportingFor']
        if cl['kind'] in {'risk','status'}:cl['kind']='statement'
    prior.validate(source,clean)
    claims={c['id']:c for c in output['claims']}
    for cl in output['claims']:
        m=cl['materiality']
        if len(m['reason'])<15:raise ValueError('Materiality needs a concrete reason')
        if m['route']=='evidence' and (m['priority']!='low' or cl['kind'] in {'decision','commitment','risk'} or cl['supersedes']):raise ValueError('Material knowledge cannot be hidden as context')
        if m['criterion']=='context' and m['route']!='evidence':raise ValueError('Context must not enter graph proposals')
        if cl['kind'] in {'decision','risk'} and m['priority']!='high':raise ValueError('Decision or material risk must remain prominent')
        if cl['supersedes'] and m['priority']!='high':raise ValueError('Changes require high-priority review')
        if m['route']=='proposal' and m['priority']=='low':raise ValueError('Low priority stays evidence-only')
        if cl['supportingFor'] and m['route']!='evidence':raise ValueError('Supporting context cannot also be a proposal')
        for target in cl['supportingFor']:
            if target not in claims or target==cl['id'] or claims[target]['materiality']['route']!='proposal':raise ValueError('Invalid context attachment')
        if cl['duplicateOf'] and claims[cl['duplicateOf']]['materiality']['route']!=m['route']:raise ValueError('Duplicate route differs')
    for edge in output['relationships']:
        if claims[edge['evidenceClaim']]['materiality']['route']!='proposal':raise ValueError('Evidence-only context cannot create a graph edge')
    for flag in output['flags']:
        if any(claims[x]['materiality']['route']!='proposal' for x in flag['claimIds']):raise ValueError('Evidence context cannot clutter review with flags')
    return output

def compile_review(source,output):
    validate(source,output)
    claims={c['id']:c for c in output['claims']};entities={e['id']:e for e in output['entities']}
    records=[];byid={};evidence=[]
    def root(cl):
        seen=set()
        while cl['duplicateOf']:
            if cl['id'] in seen:raise ValueError('Cyclic duplicate')
            seen.add(cl['id']);cl=claims[cl['duplicateOf']]
        return cl['id']
    for cl in output['claims']:
        if cl['materiality']['route']=='evidence':evidence.append(cl);continue
        base=root(cl)
        if base in byid:
            byid[base]['occurrences'].append(cl);continue
        label=entities.get(cl['subject'],{}).get('label','Unresolved topic')
        item={'number':len(records)+1,'claimId':cl['id'],'status':'needs-review','approvalEligible':False,
              'classification':'private','authority':'unknown','confidence':.5,'claim':cl,'occurrences':[cl],
              'group':{'type':entities.get(cl['subject'],{}).get('type','topic'),'id':cl['subject'],'label':label},
              'supportingEvidence':[],'flags':[f for f in output['flags'] if cl['id'] in f['claimIds']]}
        records.append(item);byid[base]=item
    for context in evidence:
        for target in context['supportingFor']:
            byid[root(claims[target])]['supportingEvidence'].append(context)
    high=[x for x in records if x['claim']['materiality']['priority']=='high']
    medium=[x for x in records if x['claim']['materiality']['priority']=='medium']
    groups={}
    for item in high:groups.setdefault(item['group']['label'],[]).append(item['number'])
    return {'recommended':high,'expanded':records,'medium':medium,'evidenceOnly':evidence,
            'recommendedGroups':groups,'counts':{'recommendedItems':len(high),'recommendedGroups':len(groups),
              'expandedProposals':len(records),'mediumItems':len(medium),'evidenceOnly':len(evidence),
              'duplicateOccurrences':sum(len(x['occurrences'])-1 for x in records)},
            'notice':'Individual items, not group cards, count toward review-volume targets. No cap hides material claims.'}
