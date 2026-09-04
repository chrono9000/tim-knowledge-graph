# Graph schema

[`graph.schema.json`](graph.schema.json) is a JSON Schema Draft 2020-12 contract for the canonical graph in [`../data/graph.json`](../data/graph.json).

Every node and edge has stable IDs, provenance through `sourceIds`, RFC 3339 creation and update timestamps, a `confidence` score from `0` to `1`, and an `authorityLevel`:

- `owner`: directly curated by the graph owner
- `primary`: original evidence or a first-hand source
- `secondary`: analysis of primary evidence
- `tertiary`: a summary or index of other sources
- `unknown`: authority has not yet been assessed

Top-level `sources` hold the reusable source details. A future agent may add records only when their `sourceIds` resolve to those entries. JSON Schema validates shape and values; repository validation must also enforce unique IDs and valid category, source, and edge endpoint references.
