# FEOS knowledge-graph operating harness

Version 1.0.0 defines how the deterministic agent turns private material into reviewable graph proposals. The machine-readable authority is [`harness.json`](harness.json); this document explains the same operating contract for reviewers.

## Purpose, scope, and boundaries

The agent identifies potentially useful knowledge, records provenance, and prepares proposals. It may import, classify, compare, stage, and validate. It may not autonomously publish, delete knowledge, call external AI services, treat inference as fact, or expose private material through GitHub Pages. A human remains responsible for accepting private knowledge and separately approving public knowledge.

## Epistemic labels

- **Fact:** a verifiable claim supported by identified evidence.
- **Decision:** a committed choice made by an explicitly identified authorized owner.
- **Preference:** an identified person's stated inclination; not a policy or objective fact.
- **Recommendation:** advice about a possible action; not an approved decision.
- **Assumption:** a working premise without enough verification.
- **Unresolved question:** an open question or issue awaiting an answer.
- **Policy:** a governing rule issued by an authorized owner or authoritative record.
- **Superseded information:** a historical statement retained after a later approved record replaces its current applicability.

If wording merely implies a conclusion, the agent must use `assumption` or `unresolved-question`, never `fact`. Repetition does not turn an unsupported claim into a fact.

## FEOS authority and source priority

FEOS is the repository's named authority model. This harness does not assign an unstated expansion to that name.

Authority ranks from highest to lowest: `owner`, `primary`, `secondary`, `tertiary`, `unknown`. Owner-approved canonical wording is strongest. Primary evidence includes direct statements by the subject or decision owner and signed or contemporaneous official records. Secondary sources analyze primary evidence. Tertiary sources summarize or recollect it. Unknown sources have not been assessed.

Within one tier, use this priority: exact approved wording; the identified decision owner or subject; a signed policy or decision record; a contemporaneous record; a later restatement; conversation recollection; deterministic extraction. Higher authority determines the recommended precedence and may raise the retained authority metadata. It never permits silent conflict resolution: both versions and both sources remain visible to the reviewer.

## Identity, ownership, naming, and deduplication

Entity and decision owners may be identified only by explicit `owner`, `decisionOwner`, or `decision_owner` fields, or an explicit `owner-of`/`decision-owner` relationship. A mention, nearby name, job title, or authorship alone does not establish ownership.

Labels are cleaned for display; IDs are stable lowercase kebab-case. Deduplication uses Unicode-normalized, case-insensitive, punctuation-insensitive canonical text. A canonical match becomes a duplicate/update proposal. It does not erase either source or replace approved wording automatically.

## Provenance, timestamps, confidence, and wording

Private staging retains source ID, filename, SHA-256 content hash, source timestamp, retrieval timestamp, authority, and confidence. Graph records retain all source IDs plus created, updated, first-seen, and last-seen timestamps. Reviews and publication add their own timestamps.

Confidence describes support for an extraction, not the probability that it is true. Extraction confidence is capped by the FEOS source tier: owner 1.0, primary 0.9, secondary 0.75, tertiary 0.6, unknown 0.5. Conflicting claims are not averaged. Anything below 0.7 requires review and remains private by default.

Normal extraction may clean whitespace for graph consistency. If a source explicitly requires exact approved wording, that exact text must be retained in the proposal and publication must not normalize or paraphrase it. Such wording still requires human approval.

## Conflict and supersession

A contradiction records the prior record, proposed record, sources, timestamps, authority comparison, and applicable harness rules. It is marked `needs-review`; the agent recommends the higher authority but does not choose silently.

Supersession is not deletion. The old wording remains traceable in staging and audit history, and the replacement must be separately approved. Historical applicability dates and provenance remain intact.

## Privacy, sensitivity, and approval

Every import starts private. Sensitive signals—credentials, government identifiers, contact details, private personal information, or confidential organization/client material—force `needs-review`, default to private, and cannot receive public approval unless the reviewer supplies the dedicated sensitive-content override. Even then, `publish` accepts only `approved-public` proposals and replaces private source details with a non-sensitive source reference.

The normal states are `pending`, `needs-review`, `approved-private`, `approved-public`, and `rejected`. Import and preview are read-only with respect to both graphs. Private approval affects only the ignored private master. Public approval marks eligibility; publication is a separate command.

## Append-only operation, audit, and recovery

Automatic deletion is forbidden. Existing sources, nodes, edges, source references, and prior approved wording may not disappear. Before an atomic replacement, the agent validates append-only behavior, unique IDs, source references, and edge endpoints. A malformed source or validation failure leaves the last valid graph unchanged. Recovery is to correct or reject the staged input and retry; destructive rollback is unnecessary because each graph write is atomic and prior Git commits remain available for the public file.

Private JSON Lines logs record the action, timestamp, harness version, proposal IDs, and affected rule IDs without copying raw conversation text. Logs, staging, raw sources, and the private master are ignored by Git and excluded from Pages.
