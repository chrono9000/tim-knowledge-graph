# Tim's Knowledge Graph

A self-contained, interactive knowledge graph for exploring connected interests and ideas across business, AI/tech, EverQuest, media/closet, automotive, real estate, finance, health/fitness, and personal topics.

## Explore

- Search labels and descriptions
- Toggle any category
- Select nodes to highlight their direct connections
- Drag nodes, pan the canvas, and zoom with mouse, touch, or the view controls
- Use `/` to focus search and `Escape` to clear it

The graph data lives in [`graph-data.js`](graph-data.js), with a deliberately simple structure for adding nodes and links. The interface has no runtime dependencies or build step.

## Run locally

Serve the repository root with any static HTTP server, then open `index.html` through that server.

## Deployment

Every push to `main` is published to GitHub Pages by the workflow in `.github/workflows/pages.yml`.
