# Daily private knowledge updates — Step 6

Nothing has been scheduled or connected to an AI service. The private master remains local; Pages remains an optional public view.

## Automatic versus manual

Running `python -m agent run-daily` scans `data/raw/`, hashes supported files, skips unchanged files, and stages new or changed conversations. It compares proposals with approved knowledge and the queue, applies the 22 FEOS rules, and prints a concise report. It never approves or publishes.

You still request/download an export, place it in the private folder, review proposals, approve private changes, and separately approve public publication. Scheduling would automate only the local scan. It cannot make exports appear. Export delivery can take days; a daily scan does not guarantee daily fresh ChatGPT data.

## First use

Open a terminal in the repository. Python 3.10+ is required; no extra packages or API key are needed.

1. Read [private viewing choices](PRIVATE_HOSTING.md).
2. If you accept the local-only design, run `python -m agent approve-access --mode local-only` yourself. Step 6 has not recorded approval for your real data.
3. Download an export through ChatGPT's supported export process. Place the ZIP in `data/raw/`, or place extracted conversation JSON files there.
4. Run `python -m agent run-daily`.
5. Run `python -m agent review`.

Accepted input: ZIP containing exactly one `conversations.json`, extracted conversation JSON arrays (including individually extracted numbered files), and UTF-8 `.txt`/`.md` notes. Attachments are never extracted. For a ZIP with numbered conversation files, manually extract only those JSON files to `data/raw/`; keep that unsupported original ZIP outside the scanned folder. The older `stage-chatgpt` command retains tagged-statement extraction; `run-daily` is the ordinary-prose path.

Project labels are retained only when present. Project instructions, files and attachments are not synchronized. Standalone notes have unknown authorship and become conservative assumptions/recommendations. ChatGPT JSON preserves roles; a user role alone does not identify Tim in a shared conversation.

## Review and approve without editing JSON

The numbered list shows wording, type, confidence, authority, source message/timestamp, explicit ownership, review reasons, and possibly conflicting claims. Numbers stay stable after approval/rejection. `python -m agent preview` gives full structured detail.

Use numbers from your own list; these numbers are examples:

```powershell
python -m agent approve-private --number 2
python -m agent approve-private --number 3 --number 4
python -m agent reject --number 5 --number 6
python -m agent approve-private --batch BATCH_ID
python -m agent reject --batch BATCH_ID
```

Approve an edge with any missing endpoint nodes; missing references fail the whole approval before writes. Owner/person nodes are separate proposals. `--all` means you reviewed every matching item. Node approval also accepts its required source record. A rejected source must be resolved before its dependent nodes can be accepted.

Ordinary prose does not become verified fact merely through import. Staging distinguishes `user-statement`; the existing graph conservatively represents unverified user statements as `assumption`. Preferences stay preferences. Named explicit decisions retain `decision-owner` relationships. “I decided” stays an unverified user statement until identity is resolved. Nearby names/job titles do not imply ownership. Recommendations stay recommendations.

Offline prose confidence is capped at 0.5, with `unknown` authority; every proposal requires review. The extractor is conservative and can miss nuances. Sentences over 500 characters are skipped, not copied or silently truncated. Exact-wording cues preserve the selected sentence verbatim. Compare important source material with the review list.

## View the private mind map

Run `python -m agent view-private` and open the displayed `http://127.0.0.1:8765/` address. The existing viewer uses its route allowlist and disables caching. Ctrl+C stops it. No private graph is copied into the public site.

## Optional public publication

After private approval, select only deliberately sanitized items:

```powershell
python -m agent approve-public --number 2
python -m agent publish
```

Only `publish` changes `data/graph.json`. Committing/pushing that deliberate public change deploys it. A daily run or private approval never does so. Sensitive items additionally require `--allow-sensitive`; prefer keeping them private. Public copies omit extraction evidence, filenames, hashes and unapproved claim histories. Referenced public endpoints need their own approval.

## Stop and recover

`python -m agent stop` creates a local kill switch. The runner checks between conversations and intake/approval/publication checks before committing. It cannot cancel a transaction already committing, or stop the viewer. `python -m agent resume` allows manual processing again; it does not install a schedule.

After interruption, rerun the daily command: completed conversations are skipped and unfinished staging transactions replay. An interrupted approved graph transaction stops the runner and requires `python -m agent recover`. This finishes only the already authorized journaled update, without new approval. Then retry the intended command. Failed files/providers are not marked processed.

## Roll back an approved private update

Run `python -m agent rollback-list`, then `python -m agent rollback-private UPDATE_ID` using an ID from the list. Roll back later private updates first. This explicit exception to normal append-only updates restores the previous private snapshot and affected review statuses. Original snapshots, wording, approvals and audit entries remain intact; unrelated newer queued proposals remain. Public-approved/published items are refused. Public rollback requires a separate deliberate public change and review.

Back up `data/private/`, `data/staging/` and `logs/` privately, preferably encrypted. Git cannot restore ignored files. Do not delete journals or lock files to recover. OS locks release on process exit. Journals grow and need a future reviewed archival policy. Use a local filesystem, not a network share or live cloud-synced runtime directory. Atomic replacement/fsync protects process interruption; underlying hardware/filesystem durability still matters.

## Proposed daily schedule — inactive

Recommend **09:00 America/New_York daily**, on Tim's computer under Tim's OS account, without overlapping runs. `python -m agent schedule-plan` displays the inactive plan. `scripts/run-daily.ps1` is a manual/scheduler entry point that sets the working directory and propagates failures; it never registers a task. After Tim approves design, machine/account and timing, Task Scheduler can invoke it with “Do not start a new instance.” Keep output in ignored private storage. Never run private intake in GitHub Actions.

## Future connectors and AI

See [source access and AI activation](SOURCE_AND_AI_DESIGN.md). A supported connector can implement `SourceProvider.conversations` and feed normalized messages into the same staging workflow. Documented access, authentication, scope and consent must precede activation. No scraping, session credential reuse, or browser extraction is supported.
