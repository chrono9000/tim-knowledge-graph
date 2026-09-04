# Tim's Knowledge Graph

A static, interactive knowledge graph with a clean data boundary for future agent-driven curation. The existing visual experience remains dependency-free and deploys directly to GitHub Pages.

## Explore

- Search labels and descriptions
- Toggle any category
- Select nodes to highlight their direct connections
- Drag nodes, pan the canvas, and zoom with mouse, touch, or the view controls
- Use `/` to focus search and `Escape` to clear it

## Architecture

```text
agent/              future agent instructions and orchestration
data/
  raw/              future immutable source captures
  processed/        future normalized, reviewable records
  graph.json        canonical graph consumed by the frontend
schemas/            JSON Schema and field documentation
scripts/            future validation and maintenance utilities
logs/               future local run logs
index.html           static application shell
app.js               graph rendering and interaction code
styles.css           visual design
```

The placeholders do not run anything. This repository contains no automatic ingestion, bot, scheduled job, or external service.

## Data flow

When the page loads, `app.js` resolves `./data/graph.json` against the current document URL and fetches it. This relative URL works both at a local server root and under the `/tim-knowledge-graph/` project path used by GitHub Pages. The JSON document contains display categories, source records, nodes, and edges; its contract is documented in [`schemas/`](schemas/README.md).

To change the graph, edit `data/graph.json` rather than JavaScript. Keep IDs unique, ensure every edge endpoint names an existing node, and attach every node and edge to at least one top-level source.

## Run locally

Serve the repository root with any static HTTP server, then open `index.html` through that server. Opening the file directly is not supported because browsers restrict JSON requests from `file:` URLs.

## Deployment

Every push to `main` is published to GitHub Pages by the workflow in `.github/workflows/pages.yml`.
