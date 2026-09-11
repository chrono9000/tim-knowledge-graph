# Step 6.5 local knowledge capture

Provider-independent intake contract and local mock; no production MCP transport, OAuth provider, hosting or schedule is activated. The historical export path remains available unchanged. This branch does not alter the public site.

## Implemented

- Strict bounded delta schema plus semantic field validation; at most 32 KiB, 20 claims, 30 entities, 400 characters per evidence excerpt. Unknown fields, duplicate JSON keys, malformed types, nonfinite numbers, invalid references, missing decision ownership, invalid deadlines and unsupported relationship endpoints are rejected.
- POST `/v1/deltas` only, bound to `127.0.0.1`. Exact Host, no browser Origin, bounded Content-Length, no chunked input, request read timeout. No remote approval, admin, read, delete, merge or publish routes.
- HMAC-SHA256 authentication for a trusted local adapter; signs method, path, body hash, timestamp, nonce and stable idempotency key. Five-minute clock window; persistent replay ledger; fresh-nonce retries return the original receipt without duplicates. Same IDs/different content fail closed.
- SQLite storage MUST be outside all Git checkouts. New stores are disabled. Transactions cover submission/idempotency/audit/job state; curator work and acknowledgment commit together. Evidence, receipts, source occurrences and proposals are append-only through SQL triggers. Audit entries form a hash chain. Local administrators can still alter the database: this is not immutable off-machine archival storage.
- Persistent adapter outbox with bounded retry/backoff and manual dead-delivery recovery. Curator retry queue with three-attempt dead jobs and local requeue. SQLite online backup, restart recovery and history verification. A local kill switch stops intake and curator work; no scheduler is installed.
- Run-once curation invokes the existing FEOS harness, authority caps, materiality rules, exact scoped deduplication, possible-conflict/supersession flags and review priority. Source occurrences remain separate. Grouping by topic, project, person or entity; recommended and expanded views; stable IDs and a review snapshot hash.

## Boundaries and limitations

The mock validates a signed assertion, not whether a quote really occurred in ChatGPT. It cannot semantically prove decision adoption or detect every paraphrased contradiction. Human review and a future authorized semantic curator remain necessary. The current worker compares its local proposal ledger only; approved private graph comparison and approval-schema translation belong to the later reviewer bridge. The bridge must preserve existing `approve-private` validation and audit, and must never grant the capture service graph write access. Capture preview items deliberately have `approvalEligible: false`.

This is plugin-ready contract metadata, not an installed ChatGPT plugin. A production MCP facade still needs initialization/tool transport, OAuth discovery and access-token validation, exact user allowlisting, per-principal limits, protected secrets, encrypted storage/backups and an integration test in the target account. Never hand the model a signing key. Restrict the serving process to the intake directory so an application defect cannot access the private graph or public checkout. The local mock has no OS-enforced sandbox or storage encryption; those are required before private production use.

Exact replay is rejected; a legitimate retry must create a new nonce while preserving the original delta. Stable claim IDs belong to source assertions; revisions use new IDs. Authority supplied in the delta is retained as asserted metadata but effective authority remains unknown and confidence is capped at 0.5. This is not a claim of validated AI extraction accuracy.

## Local operation

Run from the repository root using Python 3.12+ (standard library only):

```text
python -m agent.capture --store ../private-capture/intake.sqlite3 status
python -m agent.capture --store ../private-capture/intake.sqlite3 resume
python -m agent.capture --store ../private-capture/intake.sqlite3 serve --key-file ../private-capture/signing-key.bin
python -m agent.capture --store ../private-capture/intake.sqlite3 curate
python -m agent.capture --store ../private-capture/intake.sqlite3 review --group-by project
python -m agent.capture --store ../private-capture/intake.sqlite3 review --expanded
python -m agent.capture --store ../private-capture/intake.sqlite3 pause
python -m agent.capture --store ../private-capture/intake.sqlite3 backup ../private-backups/intake-snapshot.sqlite3
```

These are operator examples, not permission to activate anything. `serve` requires at least 32 random bytes in an external protected key file; no key is distributed in the repository. `status` is local only and contains counts, no claim text. The synthetic demo creates an ephemeral in-memory signing key and leaves its store disabled.

Recovery: stop service/curator, verify the backup's history, restore to a new external directory, pause the restored store before starting service, retain the nonce/idempotency ledger, then resume only after operator authorization. A backup rollback can omit recently accepted receipts; reconcile/replay the adapter outbox before declaring recovery complete. Do not delete evidence or edit failed payloads; append a corrected submission with new IDs. Backups need independent protection and retention limits approved by the owner.

Tests: `python -m unittest discover -s agent/tests`. All development data is synthetic. No external accounts, hosting, model APIs, schedules, graph approvals or real exports are used.
