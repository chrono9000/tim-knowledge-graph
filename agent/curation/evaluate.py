"""Grade immutable synthetic outputs; no extraction, network or graph writes."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from . import core
from ..trial_v2.evaluate import align, tuples
from ..trial_v3.evaluate import normalized, measure

LOCK_HASH = '405792cdd91421b07055903b7d2c9e84d2357ba1f4a78c0df4c73f4c53b426ae'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def assess(gold, raw):
    entity_map = {}
    used = set()
    for entity in raw['entities']:
        names = {normalized(x) for x in [entity['label'], *entity['aliases']]}
        matches = [e for e in gold['entities'] if e['type'] == entity['type'] and names &
                   {normalized(x) for x in [e['label'], *e['aliases']]}]
        target = matches[0]['id'] if len(matches) == 1 else 'unmatched-' + entity['id']
        if target in used:
            target = 'split-' + entity['id']
        used.add(target)
        entity_map[entity['id']] = target
    actual = copy.deepcopy(raw)
    for claim in actual['claims']:
        for field in ('speaker', 'subject', 'decisionOwner'):
            if claim[field] is not None:
                claim[field] = entity_map.get(claim[field], 'invalid-' + claim[field])
        if claim['commitment'] and claim['commitment']['owner'] is not None:
            claim['commitment']['owner'] = entity_map.get(claim['commitment']['owner'], 'invalid-owner')
    for rel in actual['relationships']:
        for field in ('source', 'target'):
            rel[field] = entity_map.get(rel[field], 'invalid-' + rel[field])
    mapping = align(gold, actual)
    identity = {c['id']: c['id'] for c in gold['claims']}
    def dimensions(output, cm, em):
        material = dict(output, claims=[c for c in output['claims'] if c['materiality']['route'] == 'proposal'])
        values = tuples(material, cm)
        values['entities'] = {(em[e['id']],) for e in output['entities']}
        for dimension in ('duplicates', 'changes', 'priority', 'durability', 'materiality', 'amounts'):
            values[dimension] = set()
        for claim in material['claims']:
            key = cm[claim['id']]
            for field, dimension in [('duplicateOf', 'duplicates'), ('supersedes', 'changes')]:
                if claim[field]:
                    values[dimension].add((key, cm.get(claim[field], 'invalid-target')))
            for field in ('priority', 'durability'):
                values[field].add((key, claim['materiality'][field]))
            values['materiality'].add((key, claim['materiality']['criterion']))
            if claim['amount'] is not None:
                values['amounts'].add((key, json.dumps(claim['amount'], sort_keys=True)))
        return values
    wanted = dimensions(gold, identity, {e['id']: e['id'] for e in gold['entities']})
    got = dimensions(actual, mapping, entity_map)
    metrics = {k: measure(w, got[k]) for k, w in wanted.items()}
    differences = {k: {'extra': sorted(got[k] - w, key=str), 'missing': sorted(w - got[k], key=str)} for k, w in wanted.items()}
    burden = {x[0] for x in got['claims'] - wanted['claims']}
    for code, field, refs in got['uncertaintyFlags'] - wanted['uncertaintyFlags']:
        burden.update(refs)
    for refs in got['conflicts'] - wanted['conflicts']:
        burden.update(refs)
    burden.update(x[0] for x in wanted['duplicates'] - got['duplicates'])
    return {'metrics': metrics, 'differences': differences, 'entityMap': entity_map,
            'claimMap': mapping, 'unnecessaryReviewCandidateClaims': sorted(burden),
            'unnecessaryReviewCandidateCount': len(burden)}

def grade(frozen, manifest_hash, out):
    mp = frozen / 'frozen-manifest.json'
    if sha(mp) != manifest_hash:
        raise ValueError('Manifest changed')
    manifest = json.loads(mp.read_text(encoding='utf-8'))
    digest = sha(frozen / 'output.json')
    files = manifest.get('files', {manifest.get('outputFile'): manifest.get('outputSha256')})
    if files != {'output.json': digest}:
        raise ValueError('Output changed')
    receipt = {'manifestHash': manifest_hash, 'outputHash': digest,
               'gradingStartedAt': datetime.now(timezone.utc).isoformat()}
    with (frozen / 'grading-receipt.json').open('x', encoding='utf-8') as handle:
        json.dump(receipt, handle, indent=2)
    if sha(core.ROOT / 'lock.json') != LOCK_HASH:
        raise ValueError('Expected-result lock changed')
    lock = json.loads((core.ROOT / 'lock.json').read_text())
    for name, expected_hash in lock['files'].items():
        if sha(core.ROOT.parents[1] / name.replace('\\', '/')) != expected_hash:
            raise ValueError('Locked file changed: ' + name)
    source = json.loads((core.ROOT / 'holdout-source.json').read_text())
    gold = json.loads((core.ROOT / 'holdout-expected.json').read_text())
    raw = json.loads((frozen / 'output.json').read_text(encoding='utf-8'))
    errors = []
    try:
        core.validate(source, raw)
    except Exception as exc:
        errors.append(str(exc))
    result = {'run': 'astra-curation-blind', 'receipt': receipt, 'apiCalls': 0, 'apiCostUsd': 0,
              'errors': errors, 'gatePass': False, 'lockHash': LOCK_HASH}
    try:
        core.shape(raw, core.schema())
        result.update(assess(gold, raw))
    except Exception as exc:
        errors.append('scoring: ' + str(exc))
    if not errors:
        review = core.compile_review(source, raw)
        result['reviewCounts'] = review['counts']
        result['estimate100'] = {k: v / len(source) * 100 for k, v in review['counts'].items()}
        result['conversationCount'] = len(source)
        result['accuracyGatePass'] = (result['metrics']['claims']['recall'] == 1 and
            result['metrics']['claims']['fp'] == 0 and
            result['metrics']['uncertaintyFlags']['fp'] == 0 and
            all((m[k] is None or m[k] >= .95) for m in result['metrics'].values() for k in ('precision', 'recall')))
        result['volumeGatePass'] = result['estimate100']['recommendedItems'] <= 100
        result['gatePass'] = result['accuracyGatePass'] and result['volumeGatePass']
        (out / 'Curation-review-proposals.json').write_text(json.dumps(review, indent=2), encoding='utf-8')
    result['notice'] = ('Quote alignment is followed by human source adjudication; unmatched claims are not automatically inventions. '
                        'Synthetic scenario coverage does not establish a typical-export volume estimate. Empty metric denominators are N/A.')
    (out / 'Curation-evaluation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (out / 'Curation-frozen-output.json').write_bytes((frozen / 'output.json').read_bytes())
    (out / 'Curation-freeze-manifest.json').write_bytes(mp.read_bytes())
    if sha(mp) != manifest_hash or sha(frozen / 'output.json') != digest:
        raise ValueError('Frozen artifacts changed during grading')
    return result
