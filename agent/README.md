# Controlled private intake

This package provides a manual, deterministic, review-gated intake workflow. Imports are private by default: they create proposals in ignored local storage and never change either graph. Reviewers choose what enters the private master graph and must make a separate, explicit choice before anything can enter the public graph.

Python 3.10 or newer is required. The package uses only the standard library and makes no network requests or external AI API calls.

## Storage boundary

- `data/graph.json` is the public graph consumed by GitHub Pages.
- `data/private/master-graph.json` is the ignored private master graph. It is created from the public graph on first approval.
- `data/staging/proposals.json` is the ignored proposal queue.
- `logs/intake-*.jsonl` contains ignored private audit events.
- Raw exports remain where the operator placed them. Their content is parsed in memory and is never copied into the repository.

Only the placeholder documentation in private/runtime directories is tracked. The Pages workflow uses an explicit allowlist and cannot include the private master, staging queue, raw inputs, logs, manifests, tests, or agent code.

## Supported export formats

The import command accepts UTF-8 `.txt`, `.md`, `.json`, or `.zip` files:

1. Official ChatGPT `conversations.json`: a JSON array of conversation objects using `title`, `create_time`, `update_time`, and `mapping`. Messages are read from each mapping entry's `message.author.role` and `message.content.parts` fields.
2. A single ChatGPT conversation object with the same `mapping` structure.
3. Structured project JSON with arrays named `entities`, `people`, `projects`, `decisions`, `systems`, `policies`, `events`, `openIssues`/`open_issues`, or `historicalNotes`/`historical_notes`, plus optional `relationships` or `edges`.
4. Plain-text or Markdown notes. Deterministic prefixes include `Entity`, `Project`, `Decision`, `Person`, `System`, `Policy`, `Event`, `Open issue`, `Historical note`, `Fact`, `Recommendation`, `Assumption`, and `Question`. Relationships use `Relationship: source | label | target` or `source -> label -> target`.
5. ZIP project exports containing supported files. Archives are read in memory, capped at 1,000 entries and 25 MiB uncompressed, and may not be encrypted.

Attachments, images, HTML exports, custom ChatGPT data layouts, and encrypted archives are not supported. Unsupported files inside ZIP exports are ignored.

## Commands

Run commands from the repository root:

```bash
python -m agent import /path/to/conversations.json
python -m agent preview
python -m agent preview --status needs-review
python -m agent approve-private --batch BATCH_ID
python -m agent approve-public PROPOSAL_ID
python -m agent reject PROPOSAL_ID
python -m agent publish
```

Use `--all` only after reviewing the full queue. Global path overrides (`--public-graph`, `--private-graph`, `--staging`, and `--log-dir`) support isolated testing and private deployments. `--authority-tier` on import can explicitly set source authority.

Review statuses are `pending`, `needs-review`, `approved-private`, `approved-public`, and `rejected`. Possible contradictions, duplicate updates, authority conflicts, sensitive-looking content, and confidence below 0.7 are marked `needs-review`. Approval is always manual even for ordinary `pending` items.

`approve-private` merges selected proposals only into the private master. `approve-public` also merges them into the private master but merely marks them eligible for publication. `publish` is the only command that changes `data/graph.json`; it publishes only `approved-public` records and replaces private source details with a non-sensitive provenance reference.

## Safety guarantees

- Imports and previews do not change either graph.
- Existing sources, nodes, and edges cannot be removed or overwritten.
- Every proposal retains private provenance, source and observed timestamps, confidence, authority, and first/last-seen dates.
- Writes validate append-only behavior, source references, and edge endpoints before an atomic file replacement.
- A malformed import fails before staging is changed; a failed validation leaves the existing graph file untouched.
- Re-importing identical content returns the existing batch and creates no duplicate queue entry.
- The old direct-ingestion CLI is disabled so review cannot be bypassed.

## Tests

All fixtures are synthetic:

```bash
python -m unittest discover -s agent/tests -v
```
