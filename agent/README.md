# Deterministic ingestion agent

This package reads new or changed UTF-8 `.txt`, `.md`, and `.json` files beneath `data/raw/`, extracts graph records with deterministic rules, and merges them into `data/graph.json`. Common JSON conversation exports are flattened from role/content messages. No model, external API, network request, bot, or scheduled job is used.

Python 3.10 or newer is required. The pipeline uses only the Python standard library.

## Run it

From the repository root:

```bash
python -m agent --dry-run
python -m agent
```

Dry-run prints the same change summary without writing the graph, manifest, or logs. A real run writes:

- the append-only merged graph to `data/graph.json`
- processed file hashes to `data/processed/ingestion-manifest.json`
- one JSON Lines audit log per run in `logs/`

Use `python -m agent --help` for path overrides and the optional `--authority-tier` override.

## Extraction rules

The strongest deterministic results use one record per line:

```text
Person: Jane Doe
Project: Project Atlas
Decision: Project Atlas will launch in October.
Policy: Production access must be reviewed quarterly.
Open issue: Who owns the migration checklist?
Relationship: Jane Doe | leads | Project Atlas
Project Atlas -> depends on -> Billing Platform
```

Supported prefixes are `Entity`, `Project`, `Decision`, `Person`, `System`, `Policy`, `Event`, `Open issue`, `Historical note`, `Fact`, `Recommendation`, `Assumption`, and `Question`. Markdown headings, ordinary sentences, project/system names, and contextually introduced people are also extracted. Ordinary statements are classified by explicit wording; co-mentioned named records receive a `mentioned-with` relationship.

Optional Markdown front matter or top-level JSON metadata can set `authority`, `confidence`, and `source_timestamp`. Otherwise filenames containing `official`, `policy`, `decision`, `minutes`, or `record` are primary; conversation exports are secondary; other notes are tertiary. Confidence is bounded by the source tier.

## Safety and merge behavior

- Existing source, node, and edge IDs are checked before every write and may never disappear.
- Nodes deduplicate by a punctuation-insensitive, case-insensitive canonical label.
- A changed file becomes a new immutable source version; the prior source remains in the graph.
- Matching records gain provenance and updated first/last-seen metadata, but their labels and descriptions are not overwritten.
- Unsupported, unchanged, hidden, and placeholder `README.md` files are not ingested.
- Invalid input is logged and left out of the manifest so a later run retries it.

## Tests

```bash
python -m unittest discover -s agent/tests -v
```
