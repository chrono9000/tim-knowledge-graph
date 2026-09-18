# Step 6.5B security review — 16 September 2026

Scope: code review by the implementing agent, adversarial synthetic tests, dependency audit, Cloudflare local-runtime integration, local recovery and public-file isolation. This is not an independent penetration test or a production security certification.

## Findings addressed

- Worker entry point now separates Cloudflare ExecutionContext from the injectable test HTTP client.
- Upstream OAuth fetches use manual redirect handling with success checks; Cloudflare does not support the browser fetch redirect-error mode.
- Database capacity uses D1 response metadata rather than a PRAGMA restricted by D1.
- Private bridge timestamps include created/updated/first/last seen, allowing the existing supersession history path to preserve prior wording.
- Lost delivery acknowledgments reconcile from durable local state, even if the host already stopped listing that receipt.
- Cyclic queue pagination prevents quarantined first-page records from indefinitely starving later submissions.
- Receipt key rotation preserves validation against old keyed tombstones.
- Local emulator development dependencies were patched using pinned undici/sharp overrides. npm audit then returned zero known vulnerabilities. No npm dependencies are bundled into the production Worker.
- Runtime data, local configuration, keys, databases, emulator state and encrypted backups are excluded from Git.

## Tested controls

Numeric GitHub allowlist (username ignored), client/resource/scope checks, PKCE mismatch, authorization-code replay, refresh-token reuse/family revocation, bearer epoch revocation, explicit consent, origin restrictions, unsupported tool rejection, default kill switches, free-only configuration gate, schema parity, duplicate JSON keys, transcript-field rejection, ownership/reference bounds, malformed dates, body/evidence limits, rate limit exhaustion, exact-idempotency conflict, encrypted queue bodies, signed sync replay/expiry, database outage, retention/tombstones, encrypted restore/tamper, selected private approval/rejection, stale review, supersession history and approval-journal crash recovery.

The complete local workerd test runs OAuth with intercepted synthetic identity responses, submits MCP proposals to local D1, retrieves/decrypts them through the Python polling client, backs up before acknowledgment, applies FEOS curation, shows review, approves one synthetic item, rejects another, and verifies the synthetic public graph is unchanged. A deliberately lost acknowledgment response is recovered.

## Boundaries that remain

- The hosting runtime sees plaintext during validation; a compromised host can inspect new submissions, forge signed assertions or suppress delivery. It has no private graph credentials.
- A valid caller can submit a false assertion. Authentication does not establish speaker identity, ownership, authority or truth. Effective authority remains unknown and confidence is capped by the existing curator until review.
- A Free-plan configuration string is not proof of the real billing plan. Tim must verify the account is Free; no paid upgrade is authorized. Application rate controls cannot cap all invocation costs on a paid account.
- No physical backup-device, disk-encryption or OS ACL attestation has been performed. File paths alone cannot prove these.
- Local tests use SQLite and Miniflare/workerd; they do not measure billed edge CPU or verify a real ChatGPT connection. The OAuth implementation is deliberately restricted to preregistered public clients; unsupported client behavior must be fixed before use, never bypassed.
- Hosted deletion and monitoring are implemented/designed but unscheduled. No real-data rollout until operational cleanup/notifications are separately authorized and tested.
- Cloud ChatGPT is not proven to retain a durable outbox after a failed tool call or closed chat. A successful host receipt is required.
- Private graph and staging remain local. Full claim metadata and contradictions are retained in their append-only capture ledger; the graph is an approved projection, not a transcript archive.
- Capture and graph review cannot amend FEOS governing documents. The separate amendment design requires the actual approving authority and a verified canonical Google Drive revision. Drive editing/sync is absent.

No unaddressed test failure is accepted as a pass. Account provisioning, actual OAuth interoperability, billing-plan verification and an independent recoverable key/backup setup remain deployment prerequisites.

