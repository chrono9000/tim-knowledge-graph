# Step 6 extraction contract v2

Local synthetic evaluation only. No API transport, credentials, scheduling, production approval or publication commands are included. The v1 prompt, schema, expected answers and frozen outputs remain intact.

The v2 schema separates primary-message speaker, discussed subject, decision owner and commitment owner. Entity references use a supplied synthetic registry. The registry supplies identities, not ownership. Claims retain source spans. Ownership and adoption require explicit source evidence; an unattributed choice is proposed-choice. Source statements remain unverified proposals.

Only owner-of and depends-on relationships are allowed with type-checked registry entity endpoints and source claims. Claim/statement endpoints, self-links, duplicates, decision-owner edges and conflict edges are rejected. Possible conflicts are explained review flags. The conflict scope currently covers first-person preferences from identified holders, not general third-party preference attribution.

Commitments have separate owner/date/time/source timezone/ambiguity fields. Missing or ambiguous actionable information has one field-specific flag with explanation. Flags do not replace routine manual approval; useful proposals all remain private needs-review at confidence 0.5, with no automatic approval eligibility.

`contract.RecordedExtractor.extract(messages)` retains the provider interface signature. This is an experimental schema version and isolated review format; production ingestion and approval have not been migrated. Do not pass these records into the v1 production merger without a separately reviewed conversion.

`holdout-lock.json` freezes synthetic inputs, expected answers, schema, prompt, validation code and predeclared scoring criteria before extraction. `evaluate.py` checks that lock and the extractor's manifest, writes a grading receipt, then scores original frozen responses without repair. Precision is TP/(TP+FP); recall is TP/(TP+FN). Speakers/subjects are scored separately; null identity is not a positive entity prediction. Deadlines are field tuples. Conflict flags are scored per pair, and other uncertainty flags by code/field/claim. Explanations receive separate source review rather than exact-text matching. Routine approval of a useful claim is not unnecessary review.

Scope limits: this is a small, parent-authored synthetic set with a supplied entity registry and speaker metadata. The model received no expected answers, but all cases were intentionally designed to exercise known failure classes. The fresh sub-agent obeyed an explicit read allowlist in a shared workspace, not a separate OS filesystem sandbox. Regex-based owner/evidence checks are conservative pattern checks, not a proof of semantic entailment on arbitrary language. No inference about real-chat or Luna accuracy follows until those are separately tested.

Result: 14/14 schema and validator passes, all 25 claims captured without extras, 24/25 matching classifications, no extra material-ambiguity flags or incorrect ownership/relationship outputs on this set. The predeclared 95% precision/recall gate passes. A later synthetic-only Luna trial is reasonable, subject to new explicit API authorization. Current API access remains disabled.
