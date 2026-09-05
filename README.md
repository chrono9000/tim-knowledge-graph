# Tim's Knowledge Graph

A static, interactive public knowledge graph with a separate, controlled private intake workflow. The visual experience remains dependency-free and deploys directly to GitHub Pages.

## Explore

- Search labels and descriptions
- Toggle any category
- Select nodes to highlight their direct connections
- Drag nodes, pan the canvas, and zoom with mouse, touch, or the view controls
- Use `/` to focus search and `Escape` to clear it

## Architecture

```text
agent/              deterministic extraction and review commands
data/
  raw/              ignored private source material (README tracked)
  processed/        ignored legacy/runtime manifests (README tracked)
  private/          ignored private master graph (README tracked)
  staging/          ignored review proposals (README tracked)
  graph.json        approved public graph consumed by the frontend
schemas/            public graph schema and private proposal documentation
scripts/            future maintenance utilities
logs/               ignored private intake logs (README tracked)
index.html          static application shell
app.js              graph rendering and interaction code
styles.css          visual design
```

## Controlled data flow

```text
private export -> import -> staging review -> private approval
                                   |
                                   +-> public approval -> publish -> data/graph.json
```

Import and preview never change a graph. Every accepted record first enters the ignored private master. Only proposals explicitly marked `approved-public` can be copied into `data/graph.json`, and publication strips raw filenames and content hashes from public provenance. See [`agent/README.md`](agent/README.md) for exact formats and commands.

When the page loads, `app.js` resolves `./data/graph.json` against the current document URL. This relative URL works both at a local server root and under the `/tim-knowledge-graph/` GitHub Pages project path.

## Run locally

Serve the repository root with any static HTTP server, then open `index.html` through that server. Opening the file directly is not supported because browsers restrict JSON requests from `file:` URLs.

## Deployment boundary

Every push to `main` is published by `.github/workflows/pages.yml`. Its explicit artifact allowlist includes only `index.html`, `app.js`, `styles.css`, `data/graph.json`, and `schemas/graph.schema.json`. Private sources, master data, staging proposals, logs, manifests, tests, and agent code are excluded.
