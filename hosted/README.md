# Step 6.5B — local production integration, dormant

This branch implements the production paths but has not deployed them. No accounts, real OAuth grants, hosted resources, schedules, real conversations, graph approvals or publication were activated. All approvals in tests affect disposable synthetic graphs. Google Drive is untouched.

## Components and boundaries

- `src/worker.mjs`: stateless Streamable HTTP MCP endpoint; initialize, ping, tools/list and one proposal-only tool. GET/SSE subscription and unknown tools are rejected. JSON responses, required protocol headers, origin checks and protected-resource discovery are implemented.
- `src/oauth.mjs`: narrowly scoped authorization-code/PKCE S256 and rotating refresh tokens. GitHub is identity-only, with **empty GitHub scope** and an explicit numeric ID allowlist. The application scope is `capture:write`, not a GitHub repository permission. A browser-bound consent step precedes code issuance. Unexpected upstream scopes are rejected.
- `migrations/0001.sql`: private D1 queue, hashed authorization credentials, atomic counters/nonces, immutable receipt identity and retention-limited audit. No queue browser or remote graph writer.
- `src/crypto.mjs`: standard compact JWE RSA-OAEP-256/A256GCM encryption through Web Crypto, plus HMAC envelope authentication. Hosted storage has only ciphertext and limited routing/receipt metadata. Cloudflare still processes plaintext during validation/encryption.
- `agent/capture/secure_sync.py`: outbound HTTPS polling, redirect refusal, authenticated decryption, append-only local evidence, cyclic retrieval, persistent bounded retries/quarantine, encrypted independent backup-before-ack, and lost-ack reconciliation.
- `agent/capture/bridge.py`: FEOS-backed staging, grouped review bound to the graph/staging snapshot, explicit selected private approval or rejection, preserved provenance and supersession history. No public action is exposed.
- `agent/capture/integration.py`: explicit run-once operator commands; no background service or schedule.
- `FEOS_AMENDMENTS.md`: separate, inactive amendment design; no Drive adapter or governing-document writer.

Two refinements from the planning document: **KV is not required** for this implementation, because replay, rate, authorization and revocation records use D1's transactional consistency. Also, the OAuth client is explicitly preregistered; arbitrary dynamic client registration is not exposed. No OAuth/MCP runtime package is bundled into the Worker. This small protocol implementation needs the account-specific interoperability checks listed below; it is not a claim of certification.

The current graph schema has a smaller epistemic taxonomy than the intake contract. Rich claim type, speaker, owner, deadline, asserted/effective authority, supporting evidence, uncertainty, contradictions and source occurrences remain intact in the private capture/staging ledger. The private graph is its approved projection. Explicit resolved supersession uses existing approval/history behavior; unresolved changes remain separate review items. Semantic alias resolution remains a curator/reviewer responsibility, never a text-similarity auto-merge.

## Reproduce locally

Requirements: Python 3.12+ with the pinned `requirements-local.txt`, Node 24+, and development dependencies from `package-lock.json`. All dependencies used here were downloaded for local tests only. The Worker has no npm production dependencies. The stable Miniflare release is pinned; patched `undici` and `sharp` development overrides address the audit findings.

From the repository root:

```text
python -m unittest discover -s agent/tests
```

From `hosted`, install with `npm ci --ignore-scripts`, set `FEOS_PYTHON` to the desired Python executable if it is not named `python`, then run:

```text
npm test
npm audit
```

Tests generate temporary synthetic keys and databases. They bind emulator/test bridges to **127.0.0.1 only**, intercept the GitHub identity exchange, and dispose of the processes and test data. No actual GitHub OAuth token is requested. The full flow runs both against a SQLite D1 adapter and Cloudflare workerd/Miniflare's actual local D1 binding. This proves local code/runtime integration, not live ChatGPT/account compatibility or Cloudflare CPU billing behavior.

The example Worker configuration has no account/database IDs, `workers_dev: false`, preview URLs disabled, no scheduled handler and no deployment script. Do not turn it into an active configuration before deployment authorization.

## Admission, replay and cost controls

Intake requires the environment switch AND the D1 control row enabled, and the declared billing mode `free-only`. A deployment operator must independently confirm the real account is Free; an environment label cannot enforce Cloudflare billing. Paid-plan budget alerts are not a hard spending cap. Stay on Free with no paid upgrade or paid bindings.

Limits: 32 KiB canonical delta; 40,000-byte MCP envelope; 20 claims; 30 entities; 400 characters per evidence excerpt; 20 accepted submissions per minute and 400/day/principal; 60 MCP requests/minute; 120 OAuth requests/minute globally; 120 computer requests/minute; 150 MiB live encrypted payload and 300 MiB reported D1 size. Oversized, malformed, unauthorized and unavailable-storage cases fail closed. Exact numeric GitHub identity, OAuth client, resource, scope, expiry, revocation epoch and token-family status are checked. Five-minute computer timestamps and persistent nonces reject replay.

Stable receipt IDs bind principal, conversation/capture-session and sorted claim IDs. Same identity/content returns the receipt; altered content conflicts. Receipt fingerprints use versioned HMAC keys so rotation can verify old tombstones. Keep old receipt verification keys for the 365-day tombstone window. Envelope signing keys and recipient decryption keys also need overlap until all pending items/backups using them are recoverable. Never give the conversation model a signing key.

Only the configured GitHub token/user endpoints are fetched during identity exchange; redirects are not followed. Tokens are stored by hash. Evidence is data, never executable commands. Generic errors omit payloads and credentials.

## Retention, backup and recovery

`src/storage.mjs:maintenance` is an **operator-invoked** D1 maintenance function, not a scheduled or MCP-accessible function. It deletes delivered bodies after 24 hours, undelivered bodies after 30 days, expired nonces/rates/auth state, 365-day receipt tombstones and 30-day redacted audit entries. It records undelivered expiry as an incident. Expired bodies cannot be pulled even before cleanup. Physical provider backups may retain deleted data beyond application cleanup. Hosted audit is retention-limited; canonical local evidence/audit is append-only.

No timer is active. During a later synthetic hosted trial, the operator must invoke maintenance manually through a separately reviewed administrative runner or the D1 console. Do not accept real data until deletion execution and monitoring are operational and authorized. Queue data is not indefinitely durable while the computer is offline: the maximum undelivered window is 30 days.

The polling client uses a cyclic scan, not a high-water mark that would permanently skip a failed record. Poison records are retried up to five times then quarantined, while later pages remain retrievable. Acknowledgment means local persistence plus a verified encrypted independent backup, never knowledge approval. Lost acknowledgment responses are reconciled from the local receipt ledger.

Backups use a fresh nonce and AES-256-GCM. Restore authenticates first, bounds archive size, rejects unsafe paths, verifies the evidence/audit chain, and leaves the restored store paused. A different directory is not proof of a different device: Tim must select independent storage and protect its key separately. Local operational limits are 25 MiB per auxiliary file and 100 MiB per backup; reaching them stops backup/acknowledgment and requires an explicit storage review. No silent loss or unencrypted fallback.

Keep canonical local keys/configuration outside Git on an encrypted OS-protected disk. The initial CLI uses protected files; it does not install a credential-manager integration or enforce disk encryption. Restrict the files to Tim's OS account, avoid cloud-synced locations unless separately approved, and retain an encrypted recovery copy off the computer.

## Health and alerts — design ready, notifications inactive

Signed `POST /health` returns pending count/oldest age and dormant capability status; anonymous `/live` returns generic liveness only. The local ledger records failures without exception bodies. Review health after each manual synthetic trial.

Future alert thresholds: any authentication anomaly, any quarantined receipt, failed backup/restore, missing successful contact for 24 hours, pending age 7/21/27 days, or 80% of admission/storage limits. At 100%, stop accepting affected work; never upgrade automatically. Notify once per incident and on recovery. A future private operations repository could receive generic incident notices, but no repository or notification credential exists and no notification is sent now. No independent uptime SLA is claimed.

Kill: set hosted ENABLED false and D1 enabled=0; increment the epoch to revoke OAuth families; remove the compromised computer key. Locally pause the store and disable the integration configuration; an existing workflow STOP also blocks staging/decisions. Preserve evidence. Removing a ChatGPT connection alone does not revoke backend credentials.

## Exact manual setup after authorization

1. **ChatGPT eligibility first:** check that Tim's intended account/workspace can add a custom MCP connection with OAuth. The actual connection must support a preregistered public client with PKCE. Do not create cloud resources if that cannot be confirmed. Product UI and project availability depend on account policy.
2. **Cloudflare:** choose a Free account context with MFA; no paid Workers subscription or paid bindings. Create one Worker and one private D1 database. **Do not create KV for this version.** Apply the included migration; its control row starts disabled. Use a workers.dev HTTPS origin only after authorization. Keep Pages and this repository's Pages workflow separate.
3. **GitHub:** create a dedicated OAuth App for identity only. Homepage: the approved intake origin. Callback: `https://<approved-origin>/callback`. Do not grant repository permissions or install a repository-access GitHub App. Enter Tim's verified numeric GitHub ID into ALLOWED_GITHUB_IDS. Keep the GitHub client secret in Cloudflare secrets.
4. **Computer keys:** generate a 3072-bit RSA recipient keypair locally; upload only its public JWK. Keep the private PEM and its recovery copy protected. Generate separate random 32-byte base64url computer-signing, envelope-signing, receipt-HMAC and backup-encryption keys. The backup key never goes to Cloudflare. No real key is supplied by examples or tests.
5. **Worker bindings/secrets:** DB; ORIGIN; ENABLED=false; BILLING_MODE=free-only; ALLOWED_GITHUB_IDS as a JSON array of numeric-ID strings; GITHUB_CLIENT_ID; GITHUB_CLIENT_SECRET; OAUTH_CLIENTS mapping the chosen client ID to exact redirect_uris; RECIPIENT_JWK/public RECIPIENT_KEY_ID; ENVELOPE_KEY/ENVELOPE_KEY_ID; RECEIPT_KEYS JSON map/RECEIPT_KEY_ID; SYNC_KEYS JSON map. Copy the callback used by ChatGPT from its actual connection UI; do not guess it. Never commit these values in an active configuration.
6. **ChatGPT connection:** add the HTTPS `/mcp` endpoint, configure the preregistered client ID, complete GitHub identity login and application consent, inspect the single tool, and enable it in a synthetic conversation. No app publication is required for this private test. Test account/project selection and confirmation behavior explicitly.
7. **Local configuration:** copy the local example outside Git, set a private storage root, a local read-only public-baseline copy, protected key paths and independent backup target. Confirm independent storage. The new store starts paused. Enable it only for the separately authorized synthetic test.
8. **Manual trial:** submit synthetic deltas, run poll-once and curate-once, inspect grouped review, choose individual synthetic capture IDs with the snapshot hash, explicitly approve-private or reject, inspect the private graph and audit, test restore and kill, manually perform retention cleanup. No recurring task is created.
9. **Stop after the trial:** disable host/local intake and revoke test grants. Real chats, automatic polling/curation/retention, notification delivery, production backup rotation and public publication require separate authorization.

Operator commands are exposed through `python -m agent.capture.integration --config <protected-path> <command>`: poll-once, curate-once, review, decide, backup and restore. Decisions require explicit capture IDs, expected snapshot, decision, reviewer and an interactive typed confirmation. Do not run against real proposals during the synthetic trial.

## Remaining live-test blockers

The local integration is complete for the implemented contract. Still unverified: actual GitHub OAuth App registration, ChatGPT preregistered-client compatibility and project availability, actual Cloudflare Free account settings, free-tier CPU headroom, real disk/backup independence and key recovery, and actual receipt behavior after a cloud conversation closes. These cannot be proven by synthetic local tests.

There is no claim of external penetration testing, audited custom OAuth implementation, semantic truth verification, automatic cross-chat alias resolution or reliable cloud-chat retry after the chat ends. A broader independent security review is advisable before real data. A real-chat rollout is not authorized by a synthetic hosted trial.

## Authoritative references

- [OpenAI MCP authentication](https://developers.openai.com/plugins/build/auth) and [connection workflow](https://developers.openai.com/plugins/deploy/connect-chatgpt).
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports).
- [GitHub OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps).
- [D1 transaction/batch semantics](https://developers.cloudflare.com/d1/worker-api/d1-database/).
- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) and [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/).
- [JWE](https://www.rfc-editor.org/info/rfc7516/) and [RSA-OAEP/AES-GCM algorithms](https://www.rfc-editor.org/info/rfc7518/).

