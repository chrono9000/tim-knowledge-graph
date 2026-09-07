# Safe ChatGPT export workflow

This is the Step 5 tagged-statement workflow. For Step 6 ordinary prose and incremental daily scans, follow [Daily private knowledge updates](DAILY_WORKFLOW.md). Its ZIP format limits remain the same; extracted numbered conversation JSON files are accepted individually by the daily runner.

This is the beginner-friendly path for processing a real ChatGPT export. The private master graph is the source of truth. GitHub Pages is a separate, optional public view and is **not** a private hosting service.

Do not process a real export until you have chosen and explicitly approved a private viewing mode. The recommended first choice is `local-only`: the private graph stays on Tim's computer and the viewer listens only on `127.0.0.1`. For named people who need remote access, use Tailscale Serve with restrictive tailnet access rules. Cloudflare Tunnel plus Access is a browser-friendly alternative but adds DNS, identity-provider, and policy configuration. Never use Tailscale Funnel, a public static host, or unprotected GitHub Pages for the private graph.

## 1. Download the export

In ChatGPT, open **Settings → Data Controls → Export Data**, confirm the export, and download the ZIP from the email when it arrives. Keep that ZIP private. See OpenAI's [current export instructions](https://help.openai.com/en/articles/7260999-how-do-i-export-my-data).

This workflow supports exactly:

- the official export ZIP containing exactly one file named `conversations.json` (it may be inside a folder); or
- the extracted UTF-8 `conversations.json` itself.

`conversations.json` must be a top-level array. Each usable conversation needs an `id` (or `conversation_id`) and `mapping`; its title and `create_time`/`update_time` are used when present. Message text is read from mapping entries with `message.author.role` and `message.content.parts`. Project selection recognizes top-level `project_id`, `project_title`, or `project_name`, and nested `project.id`, `project.title`, or `project.name`. Exports without project metadata can still be selected by conversation ID, title, or date.

Attachments and every ZIP member other than `conversations.json` are ignored and never extracted. HTML exports, encrypted ZIPs, multiple `conversations.json` files, and custom layouts are rejected.

## 2. Put it in the private intake folder

Move the downloaded ZIP to `data/raw/`. Everything in that folder except its README and `.gitkeep` is Git-ignored. Do not put it in a generic tracked location elsewhere in the repository.

Choose the private viewing design and record the approval locally:

```powershell
python -m agent access-plan
python -m agent approve-access --mode local-only
```

The approval record is stored under `data/private/` and is ignored by Git.

## 3. Validate and run a true dry run

```powershell
python -m agent validate-chatgpt data/raw/chatgpt-export.zip
python -m agent dry-run-chatgpt data/raw/chatgpt-export.zip
```

The dry run builds proposals only in memory and prints counts for conversations, projects, nodes, edges, duplicates, contradictions, sensitive records, low-confidence records, and failures. It writes no proposal queue, graph, log, manifest, or approval record.

Narrow the run with repeatable selectors:

```powershell
python -m agent dry-run-chatgpt data/raw/chatgpt-export.zip --conversation CONVERSATION_ID
python -m agent dry-run-chatgpt data/raw/chatgpt-export.zip --project "Project name" --title "planning"
python -m agent dry-run-chatgpt data/raw/chatgpt-export.zip --from-date 2026-01-01 --to-date 2026-06-30
```

Selectors are combined with AND; repeated conversation or project values are alternatives within that selector. Dates are inclusive and use `update_time`, falling back to `create_time`.

## 4. Create and review private proposals

Run the same selection with `stage-chatgpt`. This creates only an ignored, local proposal batch; neither graph changes.

```powershell
python -m agent stage-chatgpt data/raw/chatgpt-export.zip --project "Project name"
python -m agent review
```

The review command displays stable proposal IDs and convenient numbers. Proposals retain the private source filename, export hash, selected conversation IDs, source timestamp, first/last seen dates, confidence, authority, and every applicable harness rule. Raw messages and attachments are not stored in staging. Only explicit lines such as `Fact:`, `Decision:`, `Project:`, `Question:`, or `Relationship: source | label | target` are eligible; ordinary chat prose is ignored.

## 5. Approve or reject items

Use numbers from the latest `review` output; repeat `--number` for a batch choice. No JSON editing is needed.

```powershell
python -m agent approve-private --number 2 --number 3
python -m agent reject --number 4
python -m agent approve-private --batch BATCH_ID
```

`--all` is available but should be used only after reviewing every item. Sensitive, conflicting, superseding, low-confidence, or assumption records are marked `needs-review`. Approval never deletes prior knowledge.

If processing is interrupted before the atomic staging write, run the same command again. If staging already completed, the export hash plus selection hash finds the existing batch instead of duplicating it.

## 6. View the private master mind map

```powershell
python -m agent view-private
```

The viewer opens `http://127.0.0.1:8765/`, serves the private master in memory, disables caching, and exposes only the frontend, schema, and private graph route. It has no directory listing and returns 404 for raw exports, staging, logs, tests, or agent files. Stop it with Ctrl+C.

For a specifically authorized remote user, first configure Tailscale access controls, choose `tailscale` in `approve-access`, run the same local viewer, and proxy that local port with Tailscale Serve. Do not use Funnel. Cloudflare Tunnel plus Access is the alternative for users who cannot install Tailscale; validate its identity policy before connecting the viewer.

## 7. Separately publish deliberate public choices

Private approval is not public approval. First promote only reviewed items, then run the separate publication command:

```powershell
python -m agent approve-public --number 2
python -m agent publish
```

Sensitive proposals require the explicit `--allow-sensitive` override, but the safest rule is never to publish them. `publish` is the only command that can change `data/graph.json`; GitHub Pages deploys only that sanitized graph and four static frontend/schema files.

## Limits and recovery

- ZIP: 512 MiB maximum; `conversations.json`: 256 MiB expanded maximum.
- Export: 100,000 conversations maximum; 10,000 mapping entries per conversation.
- Unsafe ZIP paths, encrypted data, malformed JSON, and invalid conversation records fail safely. Invalid individual conversations are counted in validation and dry-run output; staging refuses a partially invalid export.
- Writes use validation and atomic replacement. The append-only guard rejects deletions and dangling references.
- Keep an encrypted backup of `data/private/`, `data/staging/`, and `logs/` if the private master matters. These local files are intentionally not in Git, so Git cannot restore them.
- The deterministic extractor is conservative and will miss useful knowledge that is not explicitly tagged. External AI services remain disabled.
