"""Offline evaluation and request preparation. No network or credential access."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..extraction import Claim, Message, DeterministicExtractor, extract_document, CATEGORIES, EPISTEMIC
from ..harness import load_harness, evaluate_proposal
from ..ingest import atomic_json_write, canonical_text, clean_label

ROOT = Path(__file__).parent
MODEL = 'gpt-5.6-luna'
INPUT_PRICE = Decimal('0.20')
OUTPUT_PRICE = Decimal('1.20')
MAX_CALLS = 16
MAX_INPUT_TOKENS = 20_000
MAX_OUTPUT_TOKENS = 6_000
CAP_USD = Decimal('1.00')
ATTRIBUTES = {'subject', 'scope', 'amount', 'unit', 'operator', 'deadlineLocal', 'deadlineTimezone',
              'deadlineCandidates', 'amountQualifier', 'adoptedRecommendation'}


@dataclass(frozen=True)
class TrialClaim(Claim):
    """Add contextual fields without changing Extractor.extract(messages)."""
    attributes: dict[str, Any] = field(default_factory=dict)
    uncertainties: tuple[str, ...] = ()
    support: tuple[dict[str, str], ...] = ()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_inputs():
    cases = json.loads((ROOT / 'inputs.json').read_text(encoding='utf-8'))
    if len(cases) != MAX_CALLS or any(c.get('synthetic') is not True for c in cases):
        raise ValueError('Only the fixed synthetic trial suite is allowed')
    return cases


def messages(case):
    return tuple(Message(**m) for m in case['messages'])


def schema():
    nullable = lambda kind: {'type': [kind, 'null']}
    attrs = {k: nullable('string') for k in sorted(ATTRIBUTES - {'amount', 'deadlineCandidates'})}
    attrs['amount'] = nullable('number')
    attrs['deadlineCandidates'] = {'type': 'array', 'items': {'type': 'string'}}
    support = {'type': 'object', 'properties': {'message_id': {'type': 'string'}, 'quote': {'type': 'string'}},
               'required': ['message_id', 'quote'], 'additionalProperties': False}
    props = {'message_id': {'type': 'string'}, 'quote': {'type': 'string'},
             'category': {'type': 'string', 'enum': sorted(CATEGORIES)},
             'epistemic': {'type': 'string', 'enum': sorted(EPISTEMIC - {'fact'})},
             'confidence': {'type': 'number'}, 'owner': nullable('string'), 'exact_wording': {'type': 'boolean'},
             'attributes': {'type': 'object', 'properties': attrs, 'required': sorted(attrs), 'additionalProperties': False},
             'uncertainties': {'type': 'array', 'items': {'type': 'string'}},
             'support': {'type': 'array', 'items': support}}
    relation = {'type': 'object', 'properties': {k: {'type': 'string'} for k in ('source', 'relationship', 'target')},
                'required': ['source', 'relationship', 'target'], 'additionalProperties': False}
    return {'type': 'object', 'properties': {'claims': {'type': 'array', 'items': {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}},
            'relationships': {'type': 'array', 'items': relation}}, 'required': ['claims', 'relationships'], 'additionalProperties': False}


def request_for(case):
    """Allowlisted input projection: gold/split names never enter the model request."""
    payload = {'context': case['context'], 'messages': case['messages']}
    request = {'model': MODEL, 'store': False, 'background': False, 'tools': [],
               'reasoning': {'effort': 'low'}, 'max_output_tokens': MAX_OUTPUT_TOKENS,
               'instructions': (ROOT / 'prompt.txt').read_text(encoding='utf-8'),
               'input': json.dumps(payload, ensure_ascii=False),
               'text': {'format': {'type': 'json_schema', 'name': 'synthetic_knowledge_proposals', 'strict': True, 'schema': schema()}}}
    # UTF-8 bytes plus framing allowance are a deliberately conservative token bound.
    if len(json.dumps(request, ensure_ascii=False).encode()) + 1024 > MAX_INPUT_TOKENS:
        raise ValueError('Trial request exceeds reserved input budget')
    return request


@dataclass
class TrialBudget:
    calls: int = 0
    reserved_usd: Decimal = Decimal('0')

    def reserve(self, request):
        if request['model'] != MODEL or request['max_output_tokens'] != MAX_OUTPUT_TOKENS or request.get('tools') or request.get('store') is not False or request.get('background') is not False:
            raise ValueError('Request differs from approved trial envelope')
        if len(json.dumps(request, ensure_ascii=False).encode()) + 1024 > MAX_INPUT_TOKENS:
            raise ValueError('Input envelope exceeded')
        amount = Decimal(MAX_INPUT_TOKENS) * INPUT_PRICE / 1_000_000 + Decimal(MAX_OUTPUT_TOKENS) * OUTPUT_PRICE / 1_000_000
        if self.calls >= MAX_CALLS or self.reserved_usd + amount > CAP_USD:
            raise ValueError('Trial budget exhausted; do not retry')
        self.calls += 1
        self.reserved_usd += amount
        return amount


class PendingModelAdapter:
    """Existing provider signature; actual model execution awaits separate approval."""
    name, version = MODEL, 'trial-1'

    def extract(self, messages: tuple[Message, ...]) -> list[Claim]:
        raise RuntimeError('External trial is not activated; no transport or credentials are configured')


class RecordedTrialAdapter:
    """Decode externally obtained results or explicitly labeled test doubles."""
    name, version = 'recorded-trial-result', '1'

    def __init__(self, response):
        self.response = response

    def extract(self, messages: tuple[Message, ...]) -> list[Claim]:
        if set(self.response) != {'claims', 'relationships'}:
            raise ValueError('Unexpected model output fields')
        if not isinstance(self.response['claims'], list) or len(self.response['claims']) > 64:
            raise ValueError('Oversized trial response')
        claims = []
        for row in self.response['claims']:
            if not isinstance(row, dict) or set(row) != set(schema()['properties']['claims']['items']['required']):
                raise ValueError('Missing or unexpected claim fields')
            c = TrialClaim(**row)
            by_id = {m.id: m for m in messages}
            if c.message_id not in by_id or not isinstance(c.quote, str) or not 1 <= len(c.quote) <= 500 or c.quote not in by_id[c.message_id].text:
                raise ValueError('Claim lacks valid bounded primary evidence')
            if c.category not in CATEGORIES or c.epistemic not in EPISTEMIC - {'fact'}:
                raise ValueError('Invalid trial classification')
            if isinstance(c.confidence, bool) or not isinstance(c.confidence, (int, float)) or not 0 <= c.confidence <= 1:
                raise ValueError('Invalid confidence')
            if not isinstance(c.attributes, dict) or set(c.attributes) != ATTRIBUTES:
                raise ValueError('Unexpected semantic fields')
            if not isinstance(c.exact_wording, bool) or (c.owner is not None and not isinstance(c.owner, str)):
                raise ValueError('Invalid claim metadata')
            for key, value in c.attributes.items():
                if key == 'deadlineCandidates':
                    valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
                elif key == 'amount':
                    valid = value is None or (type(value) in (int, float) and math.isfinite(value))
                else:
                    valid = value is None or isinstance(value, str)
                if not valid:
                    raise ValueError('Invalid semantic value')
            if not isinstance(c.uncertainties, (list, tuple)) or any(not isinstance(f, str) or len(f) > 80 for f in c.uncertainties):
                raise ValueError('Invalid uncertainty flags')
            if not isinstance(c.support, (list, tuple)) or not c.support:
                raise ValueError('Contextual proposals must cite supporting spans')
            for span in c.support:
                if not isinstance(span, dict) or set(span) != {'message_id', 'quote'} or not isinstance(span['message_id'], str) or span['message_id'] not in by_id or not isinstance(span['quote'], str) or not 1 <= len(span['quote']) <= 500 or span['quote'] not in by_id[span['message_id']].text:
                    raise ValueError('Unsupported context evidence')
            if c.epistemic == 'decision' and by_id[c.message_id].role != 'user':
                raise ValueError('An assistant recommendation cannot become the user decision')
            adoption = c.attributes.get('adoptedRecommendation')
            if adoption:
                order = list(by_id)
                if adoption not in by_id or by_id[adoption].role != 'assistant' or order.index(adoption) >= order.index(c.message_id) or adoption not in {s['message_id'] for s in c.support}:
                    raise ValueError('Adoption requires earlier assistant context and user evidence')
            claims.append(c)
        return claims


def normalize_relationships(relationships, claims):
    """Remove exact duplicates and owner-of-to-statement duplication only."""
    if not isinstance(relationships, list) or len(relationships) > 128:
        raise ValueError('Invalid or oversized relationships')
    unique = []
    for row in relationships:
        if not isinstance(row, dict) or set(row) != {'source', 'relationship', 'target'}:
            raise ValueError('Invalid relationship')
        triple = [row['source'], row['relationship'], row['target']]
        if any(not isinstance(v, str) or not 1 <= len(v) <= 500 for v in triple):
            raise ValueError('Invalid relationship values')
        if triple not in unique:
            unique.append(triple)
    statement_quotes = {c.quote for c in claims}
    clean = [r for r in unique if not (r[1] == 'owner-of' and r[2] in statement_quotes and
             any(other[0:2] == r[0:2] and other[2] != r[2] and other[2] in r[2] for other in unique))]
    return clean, len(relationships) - len(clean)


def review_recorded(case, response, *, origin):
    """Validate and stage a supplied response; never call a model or merge data."""
    if origin not in {'external-model', 'test-double'}:
        raise ValueError('Explicit response origin required')
    claims = RecordedTrialAdapter(response).extract(messages(case))
    relations, removed = normalize_relationships(response['relationships'], claims)
    actual = {'claims': [asdict(c) for c in claims], 'relationships': relations}
    return {'origin': origin, 'aiQualityEvidence': False,
            'notice': 'Origin is caller-declared; model quality requires verified run receipts and human adjudication.',
            'actual': actual, 'removedRedundantRelationships': removed,
            'proposals': proposal_rows(case, actual, origin),
            'relationshipsForReview': relations}


def baseline(case):
    provider = DeterministicExtractor()
    emitted = provider.extract(messages(case))
    claims = [{**asdict(c), 'attributes': {'subject': c.owner} if c.owner else {}, 'uncertainties': [],
               'support': [{'message_id': c.message_id, 'quote': c.quote}]} for c in emitted]
    document, _ = extract_document(messages(case), provider)
    labels = {canonical_text(clean_label(f'{c.epistemic}: {c.quote}')): c.quote for c in emitted}
    relationships = [[e.source_label, e.relationship, labels.get(canonical_text(e.target_label), e.target_label)] for e in document.edges.values()]
    return {'claims': claims, 'relationships': relationships}


def score(expected, actual):
    """Strict frozen semantic slots, not an LLM judge or a truth oracle."""
    result = dict(expectedClaims=len(expected['claims']), missedClaims=0, missedOrMisrepresentedItems=0,
                  incorrectClassifications=0, unsupportedClaimCandidates=0,
                  expectedUncertaintyFlags=0, correctUncertaintyFlags=0, spuriousUncertaintyFlags=0,
                  expectedRelationships=len(expected['relationships']), missedRelationships=0, incorrectRelationships=0)
    details = []
    matched = set()
    for gold in expected['claims']:
        candidates = [(i, c) for i, c in enumerate(actual['claims']) if (c['message_id'], c['quote']) == (gold['message_id'], gold['quote'])]
        result['expectedUncertaintyFlags'] += len(gold['uncertainties'])
        if not candidates:
            result['missedClaims'] += 1
            result['missedOrMisrepresentedItems'] += 1
            details.append({'quote': gold['quote'], 'problem': 'missing claim', 'expected': gold})
            continue
        i, candidate = candidates[0]
        matched.add(i)
        mismatches = {}
        for key in ('category', 'epistemic'):
            if candidate.get(key) != gold[key]:
                mismatches[key] = {'expected': gold[key], 'actual': candidate.get(key)}
                result['incorrectClassifications'] += 1
        attrs = {k: v for k, v in candidate.get('attributes', {}).items() if v is not None and v != []}
        unsupported = candidate.get('epistemic') in {'fact', 'decision', 'policy'} and candidate.get('epistemic') != gold['epistemic']
        for key, value in gold['attributes'].items():
            if attrs.get(key) != value:
                mismatches[key] = {'expected': value, 'actual': attrs.get(key)}
                unsupported |= key in attrs
        unsupported |= any(k not in gold['attributes'] for k in attrs)
        result['unsupportedClaimCandidates'] += int(unsupported)
        result['missedOrMisrepresentedItems'] += int(bool(mismatches))
        wanted, got = set(gold['uncertainties']), set(candidate.get('uncertainties', []))
        result['correctUncertaintyFlags'] += len(wanted & got)
        result['spuriousUncertaintyFlags'] += len(got - wanted)
        details.append({'quote': gold['quote'], 'mismatches': mismatches, 'missingFlags': sorted(wanted - got),
                        'extraFlags': sorted(got - wanted), 'unsupportedCandidate': bool(unsupported)})
    extras = [c for i, c in enumerate(actual['claims']) if i not in matched]
    result['unsupportedClaimCandidates'] += len(extras)
    normalize = lambda r: tuple(canonical_text(v) for v in r)
    want = {normalize(r) for r in expected['relationships']}
    got = {normalize(r) for r in actual['relationships']}
    result['missedRelationships'], result['incorrectRelationships'] = len(want-got), len(got-want)
    return {'metrics': result, 'details': details, 'extraClaimsForAdjudication': extras,
            'incorrectRelationships': [list(r) for r in sorted(got-want)], 'missedRelationships': [list(r) for r in sorted(want-got)]}


def proposal_rows(case, actual, provider_name):
    harness = load_harness()
    source = {'authorityLevel': 'unknown', 'confidence': 0.5}
    rows = []
    for i, c in enumerate(actual['claims'], 1):
        flags = sorted(set(c.get('uncertainties', [])) | {'low-confidence', 'context-interpretation'})
        reasons = flags + (['possible-contradiction'] if 'possible-conflict' in flags else [])
        record = {'label': c['quote'][:100], 'description': c['quote'], 'statementType': 'assumption' if c['epistemic'] == 'user-statement' else c['epistemic'],
                  'entityType': 'decision' if c['category'] == 'decision' else 'entity', 'confidence': 0.5}
        policy = evaluate_proposal(harness, record, source, reasons)
        rows.append({'number': i, 'status': 'needs-review', 'approvalEligible': False, 'classification': 'private',
                     'provider': provider_name, 'caseId': case['id'], 'claim': c,
                     'provenance': {'caseHash': fingerprint(case), 'sourceMessage': c['message_id'], 'context': case['context'],
                                    'sourceTimestamp': next(m['timestamp'] for m in case['messages'] if m['id'] == c['message_id'])},
                     'policyDecision': policy, 'evaluatedRuleIds': [r['id'] for r in harness['rules']], 'reviewReasons': flags})
    return rows


def prepare(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = load_inputs()
    gold = {c['id']: c for c in json.loads((ROOT / 'expected.json').read_text(encoding='utf-8'))}
    aggregates = {}; results = []; staged = []; plans = []; budget = TrialBudget()
    for case in cases:
        request = request_for(case)
        budget.reserve(request)
        plans.append({'caseId': case['id'], 'request': request, 'requestHash': fingerprint(request)})
        actual = baseline(case)
        assessment = score(gold[case['id']], actual)
        results.append({'id': case['id'], 'split': case['split'], **assessment})
        staged.append({'id': case['id'], 'proposals': proposal_rows(case, actual, 'offline-prose'), 'relationshipsForReview': actual['relationships']})
        totals = aggregates.setdefault(case['split'], {k: 0 for k in assessment['metrics']})
        for key, value in assessment['metrics'].items():
            totals[key] += value
    report = {'runType': 'REAL_OFFLINE_BASELINE_NOT_AI', 'externalModelRun': False, 'cases': results, 'metricsBySplit': aggregates,
              'actualCostUsd': '0', 'proposedUpperBoundUsd': str(budget.reserved_usd), 'capUsd': str(CAP_USD),
              'inputsHash': fingerprint(cases), 'expectedHash': fingerprint(gold), 'promptHash': fingerprint((ROOT/'prompt.txt').read_text()),
              'notice': 'Unsupported candidates require human adjudication. Exact-span/slot scoring may overcount paraphrases. No mock scores demonstrate AI quality.'}
    for name, value in [('baseline.json', report), ('baseline-proposals.json', staged), ('pending-requests.json', plans)]:
        atomic_json_write(output / name, value)
    return report


def main():
    parser = argparse.ArgumentParser(description='Prepare an offline synthetic trial; no external model activation command exists.')
    parser.add_argument('--output', type=Path, default=ROOT.parents[1] / 'data/private/ai-trial')
    args = parser.parse_args()
    report = prepare(args.output)
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    main()
