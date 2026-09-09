"""Conservative local materiality gate; source evidence is retained, never deleted."""
import re

CRITERIA=['decision','ownership','commitment','preference','constraint','risk','question','relationship','durable-fact','change','status','context']

def classify_materiality(text,category='statement',epistemic='user-statement'):
    t=text.casefold()
    consequence=bool(re.search(r'\b(block\w*|delay\w*|deadline|risk|prevent\w*|cancel\w*|miss\w*|responsible|must|overdue)\b',t))
    context=bool(re.search(r'\b(waiting|waited|acknowledg\w*|thank\w*|just arrived|stepped out|cup of|coffee|on my desk|for your information)\b',t))
    if context and not consequence and category not in {'commitment','decision','constraint','risk','responsibility','superseded'}:
        return {'standalone':False,'criterion':'context','durability':'temporary','priority':'low',
                'reason':'Temporary logistics or acknowledgment; no material consequence is established.'}
    mapping={'decision':'decision','commitment':'commitment','responsibility':'ownership','preference':'preference',
             'constraint':'constraint','policy':'constraint','risk':'risk','question':'question','superseded':'change','relationship':'relationship'}
    criterion=mapping.get(category,'durable-fact')
    if re.search(r'\b(risk|overdue|blocks|blocked|delay|delayed)\b',t):criterion='risk'
    if re.search(r'\b(owns|owner of|responsible for)\b',t):criterion='ownership'
    if context and consequence:criterion='status' if category not in {'commitment','risk','constraint'} else category
    high=criterion in {'decision','ownership','risk','change','status'} or (criterion=='commitment' and bool(re.search(r'\b(by|before|tomorrow|today|monday|tuesday|wednesday|thursday|friday|deadline|at \d)\b',t)))
    if criterion=='question' and consequence:high=True
    return {'standalone':True,'criterion':criterion,'durability':'temporary' if criterion in {'commitment','status'} else 'durable',
            'priority':'high' if high else 'medium','reason':'Establishes '+criterion.replace('-',' ')+' supported by the source.'}

def group_keys(text,owner=None,label=None):
    keys=[]
    for project in re.findall(r'\bProject [A-Z][A-Za-z0-9-]*',text):keys.append('project:'+project)
    if owner:keys.append('person:'+owner)
    if label:keys.append('entity:'+label)
    if not keys:keys=['topic:Unassigned']
    return sorted(set(keys))
