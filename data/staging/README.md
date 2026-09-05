# Proposal staging

`proposals.json` is created locally during import and is ignored by Git. It stores reviewable derived records and provenance, not raw conversation text. Never publish this directory.

Each proposal also records its operating-harness version, applicable rule IDs and topics, authority-precedence recommendation, privacy eligibility, and review requirement. Supersession and contradiction proposals retain both previous and proposed records for traceability.
