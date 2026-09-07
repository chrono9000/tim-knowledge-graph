# Step 6 pre-commit privacy/security review

Reviewed 2026-09-07 against Step 5 commit `6cfd9b4e6aaf8b3241b3c8c497af5e6c2036bca7`.

- 64 standard-library tests pass locally on Windows. Existing tests remain; public-approval tests now explicitly perform prior private approval. CI runs the same synthetic suite on Windows and Linux.
- No real private exports, client/employee/family/health/compensation material or credentials were used as fixtures. New example conversations use synthetic people and projects only.
- Offline prose extraction is the default; the inactive adapter contains no transport or credential lookup. Model output cannot bypass bounded source-span validation, FEOS or approval.
- Private/public paths must be distinct; link/junction runtime paths fail closed. An OS lock serializes supported CLI writes and stable-number selection. Process-exit and contention tests pass.
- Durable before/after journals and atomic replacement protect multi-file operations. Staging retries are idempotent. Interrupted graph approvals require explicit recovery. Explicit private rollback retains historical snapshots and audit entries.
- Public approval requires private approval first. Publication is a separate command and excludes merged private claim history, private filenames, hashes and extraction evidence. Tests exercise these boundaries.
- Raw input, private state, journals, manifests, proposals, logs, lock files and common secret files are ignored by Git. The Pages workflow is unchanged and copies exactly five public files. Public graph, frontend and public schema must match the Step 5 bytes before commit.
- The daily launcher registers no scheduled task. No external extraction, scheduler or private hosting service was activated.

Known boundaries: conservative sentence extraction can miss knowledge and flag false conflicts; all offline prose has unknown authority/low confidence. Split ZIPs require manual extraction of conversation JSON. Runtime snapshots grow and need private backups. Filesystem administrators and in-process Python extensions are trusted. The legacy low-level ingestion module is outside this supported recurring workflow; use `python -m agent`, never schedule the legacy module. This is a code/test review, not a penetration test or a guarantee against local administrator compromise. Deployment/artifact/live-site verification is recorded separately after push.
