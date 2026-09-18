# Governed FEOS amendment workflow — design only

No amendment executor, Drive client, Drive credential, synchronization timer or document-writing tool is installed by Step 6.5B.

The vault can identify a potential inconsistency or improvement and draft an amendment in a separate private amendment inbox. Ordinary knowledge approval cannot approve an amendment. Canonical approved FEOS documents remain in Google Drive; local extracts are explicitly dated reference copies, never a replacement source of truth.

Each draft must contain:

- Amendment ID and immutable version/hash.
- Canonical Google Drive document ID, revision/version, section locator and current wording verbatim. If no authorized current copy is available, mark the draft blocked awaiting a reference; do not invent wording.
- Proposed wording verbatim and an exact diff.
- Reason, limited supporting evidence and references.
- Governing level: Ethos, Constitution, Doctrine, framework or procedure.
- Affected entity and scope.
- Required approving authority and the canonical rule establishing that authority. No default assumption that the submitter or an ordinary graph reviewer has it.
- Higher-level compatibility analysis, conflicts, affected lower-level documents and migration implications.
- Originator, timestamps, reviewers, decisions and dissent, all append-only.

State machine: draft -> authority/reference validation -> awaiting authority review -> rejected OR approved-in-principle -> separately authorized canonical update -> verified canonical revision. Approval-in-principle changes no governing text. Every revision becomes a new immutable proposal version and invalidates approvals for previous wording.

An authorized future editor must refetch the canonical Drive revision, verify the current wording and expected version, obtain the specific document-edit authorization, apply only the approved diff, verify the resulting document, and record the new canonical revision and evidence of the approving authority. Stale text, unknown authority, or higher-level conflict blocks application. Changes to an Ethos or Constitution do not derive authority from a lower-level procedure.

The knowledge graph may record a pending amendment as a proposal with its status. It must not treat proposed wording as current policy. Only a verified canonical update can generate a new ordinary knowledge proposal reflecting the approved policy, still subject to graph review.

Synthetic example: a procedure draft proposes changing a review window from five days to seven. The amendment must quote the actual authorized current reference, identify which entity's procedure applies, identify its designated procedure owner, and check any Constitution/Doctrine constraints. No assumed approval by Tim, no inferred Drive document ID and no automatic edit.

Future recovery uses an append-only amendment ledger and canonical document revision history; rollback itself requires appropriate authority. Google Drive access remains inactive until separately authorized.

