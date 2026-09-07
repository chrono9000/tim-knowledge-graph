"""Provider-independent, evidence-bound proposal extraction; offline by default."""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, asdict
from typing import Protocol

from .ingest import CandidateNode, CandidateEdge, ExtractedDocument, canonical_text, clean_label

CATEGORIES = {'person', 'role', 'entity', 'project', 'system', 'decision', 'preference', 'constraint',
              'responsibility', 'commitment', 'question', 'risk', 'policy', 'superseded', 'relationship', 'statement'}
EPISTEMIC = {'fact', 'user-statement', 'decision', 'preference', 'recommendation', 'assumption',
             'unresolved-question', 'policy', 'superseded'}
NAME = r'[A-Z][a-z]+(?: [A-Z][a-z]+){0,2}'
DECISION = re.compile(rf'^({NAME}) (?:decided|has decided|made the decision) to (.+)', re.S)
OWNER = re.compile(rf'^({NAME}) (?:owns|is the owner of) (.+)', re.S)
RELATION = re.compile(rf'^({NAME}) (owns|is the owner of|leads|manages|is responsible for|works on|uses) (.+)', re.S)


@dataclass(frozen=True)
class Message:
    id: str
    role: str
    text: str
    timestamp: str


@dataclass(frozen=True)
class Claim:
    message_id: str
    quote: str
    category: str
    epistemic: str
    confidence: float = 0.5
    owner: str | None = None
    exact_wording: bool = False


class Extractor(Protocol):
    name: str
    version: str
    def extract(self, messages: tuple[Message, ...]) -> list[Claim]: ...


def classify(text, role):
    lower = text.casefold()
    if text.endswith('?'):
        return 'question', 'unresolved-question'
    if role != 'user':
        return 'statement', 'recommendation' if re.search(r'\b(should|recommend|suggest|could|consider)\b', lower) else 'assumption'
    if re.search(r'\b(recommend|suggest|should|could|consider)\b', lower):
        return 'statement', 'recommendation'
    if re.search(r'\b(prefer|prefers|preference|would rather|like to)\b', lower):
        return 'preference', 'preference'
    if re.search(r'\b(assume|assuming|maybe|perhaps|might|probably)\b', lower):
        return 'statement', 'assumption'
    if DECISION.match(text):
        return 'decision', 'decision'
    if re.search(r'\b(decided|decision|chosen)\b', lower):
        return 'decision', 'user-statement'  # No named decision owner; review must resolve identity.
    for pattern, category in [
        (r'\b(superseded|supersedes|replaced by|no longer current|obsolete)\b', 'superseded'),
        (r'\b(policy|must always|must never)\b', 'policy'),
        (r'\b(risk|danger|concern|at risk)\b', 'risk'),
        (r'\b(cannot|must|limited to|no more than|constraint)\b', 'constraint'),
        (r'\b(responsible for|responsibility)\b', 'responsibility'),
        (r'\b(will|promise|commit to|committed to)\b', 'commitment'),
        (r'\b(project)\b', 'project'),
        (r'\b(system|platform|database|service|api)\b', 'system'),
        (r'\b(manager|director|engineer|lead|role)\b', 'role'),
    ]:
        if re.search(pattern, lower):
            return category, 'superseded' if category == 'superseded' else 'user-statement'
    if RELATION.match(text):
        return 'relationship', 'user-statement'
    return 'statement', 'user-statement'


class DeterministicExtractor:
    name = 'offline-prose'
    version = '1'
    def extract(self, messages):
        claims = []
        for message in messages:
            if message.role not in {'user', 'assistant'}:
                continue
            for sentence in re.split(r'(?<=[.!?])\s+|\n+', message.text):
                sentence = sentence.strip()
                if len(sentence) < 8:
                    continue
                # Do not truncate canonical wording or copy a large transcript as a node.
                if len(sentence) > 500:
                    continue
                category, epistemic = classify(sentence, message.role)
                decision = DECISION.match(sentence)
                owner = OWNER.match(sentence)
                claims.append(Claim(message.id, sentence, category, epistemic, 0.5,
                                    decision[1] if decision and epistemic == 'decision' else owner[1] if owner and message.role == 'user' else None,
                                    bool(re.search(r'\b(exact wording|verbatim|do not paraphrase)\b', sentence, re.I))))
        return claims


class InactiveAIAdapter:
    """Contract test adapter. It has no HTTP client, keys, or activation switch."""
    name = 'inactive-ai'
    version = '1'
    def __init__(self, mock_response=None):
        self.mock_response = mock_response

    def extract(self, messages):
        if self.mock_response is None:
            raise RuntimeError('AI extraction is inactive. Separate privacy, model, budget and credential approval is required.')
        return [Claim(**item) for item in self.mock_response]


def extract_document(messages: tuple[Message, ...], provider: Extractor):
    """Constrain untrusted provider output before it can reach the FEOS harness."""
    by_id = {m.id: m for m in messages}
    claims = provider.extract(messages)
    if not isinstance(claims, list) or len(claims) > 5000:
        raise ValueError('Invalid or oversized extraction result')
    document = ExtractedDocument(is_conversation=True)
    evidence = {}
    for claim in claims:
        if not isinstance(claim, Claim) or claim.category not in CATEGORIES or claim.epistemic not in EPISTEMIC:
            raise ValueError('Invalid extraction claim')
        message = by_id.get(claim.message_id)
        if not message or not isinstance(claim.quote, str) or not 8 <= len(claim.quote) <= 500 or claim.quote not in message.text:
            raise ValueError('Claim lacks a bounded verbatim source span')
        if isinstance(claim.confidence, bool) or not isinstance(claim.confidence, (float, int)) or not 0 <= claim.confidence <= 1:
            raise ValueError('Invalid extraction confidence')
        if not isinstance(claim.exact_wording, bool):
            raise ValueError('Invalid exact-wording marker')
        category, epistemic = classify(claim.quote, message.role)
        # Providers cannot promote prose to evidence-backed facts or authoritative policies.
        if claim.epistemic in {'fact', 'policy', 'decision', 'preference', 'recommendation'} and claim.epistemic != epistemic:
            raise ValueError('Provider attempted unsupported epistemic promotion')
        if message.role not in {'user', 'assistant'}:
            raise ValueError('Untrusted system/tool message cannot establish knowledge')
        decision = DECISION.match(claim.quote)
        owner = OWNER.match(claim.quote)
        explicit_owner = decision[1] if decision and epistemic == 'decision' else owner[1] if owner and message.role == 'user' else None
        if claim.owner and claim.owner != explicit_owner:
            raise ValueError('Ownership is not explicitly supported by this source')
        statement = epistemic if epistemic != 'user-statement' else 'assumption'
        entity = {'person': 'person', 'project': 'project', 'system': 'system', 'decision': 'decision',
                  'policy': 'policy', 'question': 'open-issue'}.get(category, 'historical-note')
        # Type in identity keeps an assistant suggestion distinct from a user's decision.
        label = f'{epistemic}: {claim.quote}'
        if len(label) > 110:
            label = label[:90] + ' ' + hashlib.sha256(label.encode()).hexdigest()[:12]
        exact = claim.exact_wording or bool(re.search(r'\b(exact wording|verbatim|do not paraphrase)\b', claim.quote, re.I))
        document.add_node(CandidateNode(label, claim.quote, entity, statement, min(claim.confidence, 0.5), exact))
        key = canonical_text(clean_label(label))
        info = {**asdict(claim), 'epistemic': epistemic, 'category': category, 'role': message.role,
                'sourceTimestamp': message.timestamp, 'provider': provider.name, 'providerVersion': provider.version,
                'owner': explicit_owner, 'decisionOwner': explicit_owner if epistemic == 'decision' else None}
        evidence.setdefault(key, []).append(info)
        mentions = [(name, 'project') for name in re.findall(r'\bProject [A-Z][A-Za-z0-9-]*', claim.quote)]
        mentions += [(name, 'system') for name in re.findall(r'\b[A-Z][A-Za-z0-9-]* (?:System|Platform|Database|Service|API)\b', claim.quote)]
        role_match = re.match(rf'^({NAME}) is (?:the |a |an )?.*\b(manager|director|engineer|lead)\b', claim.quote)
        if role_match:
            mentions.append((role_match[1], 'person'))
        for name, kind in mentions:
            document.add_node(CandidateNode(name, f'Mentioned in: {claim.quote}', kind, 'assumption', 0.5))
            evidence.setdefault(canonical_text(clean_label(name)), []).append(info)
        if explicit_owner:
            relation = 'decision-owner' if epistemic == 'decision' else 'owner-of'
            document.add_node(CandidateNode(explicit_owner, f'Explicitly named in an {relation} statement.', 'person', 'assumption', 0.5))
            document.add_edge(CandidateEdge(explicit_owner, label, relation, 0.5, True, statement))
        match = RELATION.match(claim.quote)
        if match and message.role == 'user' and epistemic == 'user-statement':
            subject, relation, target = match.groups()
            target = target.rstrip('.!?')
            if len(target) <= 90:
                for name, kind in [(subject, 'person'), (target, 'project' if 'project' in target.casefold() else 'entity')]:
                    document.add_node(CandidateNode(name, f'Explicitly named in: {claim.quote}', kind, 'assumption', 0.5))
                document.add_edge(CandidateEdge(subject, target, 'owner-of' if relation in {'owns', 'is the owner of'} else relation, 0.5, True, 'assumption'))
    return document, evidence
