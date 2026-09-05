# Graph schema

[`graph.schema.json`](graph.schema.json) is a JSON Schema Draft 2020-12 contract for the canonical graph in [`../data/graph.json`](../data/graph.json).

Every node and edge has stable IDs, provenance through `sourceIds`, RFC 3339 timestamps, a `confidence` score from `0` to `1`, and an `authorityLevel`. Agent-created records also carry `firstSeen`, `lastSeen`, and a `statementType` that distinguishes facts, decisions, recommendations, assumptions, unresolved questions, and policies. Nodes use `entityType` to distinguish entities, projects, decisions, people, systems, policies, events, open issues, and historical notes.

Agent-created source versions preserve `filename`, `sourceTimestamp`, and a SHA-256 `contentHash`. A new source version is added when file contents change; an older source record is never replaced.

Authority levels are:

- `owner`: directly curated by the graph owner
- `primary`: original evidence or a first-hand source
- `secondary`: analysis of primary evidence
- `tertiary`: a summary or index of other sources
- `unknown`: authority has not yet been assessed

Top-level `sources` hold the reusable source details. A future agent may add records only when their `sourceIds` resolve to those entries. JSON Schema validates shape and values; repository validation must also enforce unique IDs and valid category, source, and edge endpoint references.

[`intake-proposal.schema.json`](intake-proposal.schema.json) documents the private staging format, including review status, proposed and previous records, provenance, timestamps, confidence, and authority. It is repository documentation only and is deliberately excluded from the public Pages artifact.
