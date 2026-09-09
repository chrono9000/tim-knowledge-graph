"""Inactive trial scaffolding. External transport is disabled; mock tests only."""
import json
import os
from decimal import Decimal

from . import evaluate as e

RUNTIME = e.ROOT.parents[1] / 'data/private/ai-trial/luna'
FROZEN = {
    'inputsHash': '211f4a0de600a2c613ce75e5a26551c26204440adc9c699677d7aa325f5de8f3',
    'expectedHash': 'ff33795e620ef2f2aa851b032867565547efa798d04368f083ffe9952bee3bf6',
    'promptHash': '6333040e652209ee6bdf60a1636f1c95b5eb704946a5eb5b7ad281b9cf5c39d7',
}


def suite():
    cases = e.load_inputs()
    gold = {c['id']: c for c in json.loads((e.ROOT/'expected.json').read_text(encoding='utf-8'))}
    observed = dict(inputsHash=e.fingerprint(cases), expectedHash=e.fingerprint(gold),
                    promptHash=e.fingerprint((e.ROOT/'prompt.txt').read_text()))
    if observed != FROZEN:
        raise ValueError('Frozen synthetic inputs, answers or prompt changed; stop')
    return cases, gold


def diagnostics(expected, actual):
    """Additional requested dimensions; candidates still require human adjudication."""
    counts = dict(speakerResolutionErrors=0, deadlineErrors=0, missedConflictFlags=0,
                  correctConflictFlags=0, spuriousConflictFlags=0, incompleteClassifications=0,
                  incorrectClaimCandidates=0)
    for gold in expected['claims']:
        found = next((c for c in actual['claims'] if (c['message_id'], c['quote']) == (gold['message_id'], gold['quote'])), None)
        attrs = found.get('attributes', {}) if found else {}
        wanted = gold['attributes']
        counts['speakerResolutionErrors'] += int(attrs.get('subject') != wanted.get('subject'))
        date_keys = ('deadlineLocal', 'deadlineTimezone', 'deadlineCandidates')
        counts['deadlineErrors'] += int(any((attrs.get(k) or None) != (wanted.get(k) or None) for k in date_keys))
        w = 'possible-conflict' in gold['uncertainties']
        g = bool(found and 'possible-conflict' in found.get('uncertainties', []))
        counts['missedConflictFlags'] += int(w and not g)
        counts['correctConflictFlags'] += int(w and g)
        counts['spuriousConflictFlags'] += int(g and not w)
        counts['incompleteClassifications'] += int(not found or any(found.get(k) != gold[k] for k in ('category', 'epistemic')))
    assessment = e.score(expected, actual)
    counts['incorrectClaimCandidates'] = sum(bool(d.get('mismatches')) for d in assessment['details']) + len(assessment['extraClaimsForAdjudication'])
    return {**assessment, 'additionalMetrics': counts}


def post(request, key):
    raise RuntimeError('API access and billing are not authorized; transport removed')


def decode(raw):
    def invalid_constant(value):
        raise ValueError('Non-finite JSON number')
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError('Duplicate JSON field')
            obj[k] = v
        return obj
    return json.loads(raw, parse_constant=invalid_constant, object_pairs_hook=unique)


def usage_cost(body):
    usage = body['usage']
    i, o = usage['input_tokens'], usage['output_tokens']
    cached = usage.get('input_tokens_details', {}).get('cached_tokens', 0)
    if any(type(v) is not int or v < 0 for v in (i, o, cached)) or cached > i:
        raise ValueError('Invalid usage')
    if i > e.MAX_INPUT_TOKENS or o > e.MAX_OUTPUT_TOKENS:
        raise ValueError('Token envelope exceeded')
    return (Decimal(i-cached)*e.INPUT_PRICE + Decimal(cached)*Decimal('0.02') + Decimal(o)*e.OUTPUT_PRICE)/1_000_000


def run():
    return {'status': 'disabled-by-user', 'calls': 0, 'actualCostUsd': '0'}


def _run_reserved():
    """Offline test-double reservation logic; live transport is disabled."""
    cases, gold = suite()
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        return {'status': 'blocked-missing-api-key', 'model': e.MODEL, 'calls': 0,
                'actualCostUsd': '0', 'reliability': 'Not assessed; no model execution.'}
    RUNTIME.mkdir(parents=True, exist_ok=True)
    # Permanent exclusive run marker prevents concurrent calls and rerunning after crashes.
    with (RUNTIME/'run-once.lock').open('x') as marker:
        marker.write('Approved synthetic-only Luna trial. Never auto-remove or retry.\n')
        marker.flush()
        os.fsync(marker.fileno())
    ledger = {'model': e.MODEL, 'capUsd': '1.00', 'frozen': FROZEN, 'calls': [],
              'status': 'running', 'actualCostUsd': '0', 'reservedUsd': '0'}
    budget = e.TrialBudget()
    for index, case in enumerate(cases, 1):
        try:
            suite()  # Recheck local fixture integrity before every call.
            request = e.request_for(case)
            reservation = budget.reserve(request)
            record = {'caseId': case['id'], 'requestHash': e.fingerprint(request),
                      'reservedUsd': str(reservation), 'status': 'reserved-no-retry'}
            ledger['calls'].append(record)
            ledger['reservedUsd'] = str(budget.reserved_usd)
            e.atomic_json_write(RUNTIME/'ledger.json', ledger)
            e.atomic_json_write(RUNTIME/f'{index:02}-request.json', request)
            raw = post(request, key)
            # Preserve raw result before validation; never commit runtime files.
            (RUNTIME/f'{index:02}-raw.json').write_bytes(raw)
            body = decode(raw)
            record['returnedModel'] = body.get('model')
            record['responseId'] = body.get('id')
            cost = usage_cost(body)
            record['usage'] = body['usage']
            record['actualCostUsd'] = str(cost)
            ledger['actualCostUsd'] = str(Decimal(ledger['actualCostUsd'])+cost)
            if Decimal(ledger['actualCostUsd']) > e.CAP_USD:
                raise ValueError('Cost cap exceeded')
            if body.get('model') != e.MODEL or body.get('status') != 'completed':
                raise ValueError('Unexpected model or incomplete response')
            output = body['output']
            if any(o.get('type') not in {'reasoning', 'message'} for o in output):
                raise ValueError('Unexpected tool or output type')
            content = [c for o in output if o.get('type') == 'message' for c in o.get('content', [])]
            if len(content) != 1 or content[0].get('type') != 'output_text':
                raise ValueError('Refusal or unexpected output schema')
            parsed = decode(content[0]['text'])
            review = e.review_recorded(case, parsed, origin='external-model')
            review['assessment'] = diagnostics(gold[case['id']], review['actual'])
            e.atomic_json_write(RUNTIME/f'{index:02}-review.json', review)
            record['status'] = 'needs-review'
            e.atomic_json_write(RUNTIME/'ledger.json', ledger)
            print(json.dumps({'case': index, 'status': record['status'], 'actualCostUsd': ledger['actualCostUsd']}), flush=True)
        except Exception as exc:
            # Exception messages may contain provider data; emit only the exception class.
            ledger['status'] = 'stopped-validation-or-transport-error'
            ledger['errorType'] = type(exc).__name__
            ledger['costMayBeIncomplete'] = True
            e.atomic_json_write(RUNTIME/'ledger.json', ledger)
            return ledger
    ledger['status'] = 'completed-awaiting-human-adjudication'
    e.atomic_json_write(RUNTIME/'ledger.json', ledger)
    return ledger


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
