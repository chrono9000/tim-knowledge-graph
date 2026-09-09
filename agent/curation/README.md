# Step 6 materiality refinement

This is a synthetic-only extraction contract and review compiler. It does not call a model or write an approved graph. API access and scheduling remain inactive. Step 7 has not begun.

The prompt separates speaker, subject, decision owner and commitment owner. It requires exact evidence, controlled relationships, contextual adoption of advice, specific uncertainty reasons and explicit supersession. A materiality gate routes durable or consequential knowledge into high/medium review and incidental context into source evidence. Temporary commitments and blockers remain material. There is no quota that hides material facts.

`core.compile_review` consolidates declared duplicate occurrences across conversations, preserving source occurrences. It numbers only material proposals, groups them by subject, and returns recommended high-priority and expanded medium-priority views. The experimental output remains private, needs-review and approval-ineligible; it is not silently imported into the operational queue.

The operational offline extractor uses `agent.materiality`, a conservative phrase-based fallback. Its interpretation quality is not equivalent to the blind Astra test. `agent.review_groups` creates private queue snapshots with exact duplicate grouping and rejects stale or altered selections. Semantic deduplication across aliases is evaluated in the synthetic provider contract; arbitrary offline paraphrases are not automatically merged.

For a future explicitly authorized manual review, create a private snapshot with `python -m agent.intake review-plan --group-by project --view recommended --output data/private/review-plan.json` (include the normal configured paths/options). `--view expanded` includes medium-priority items. The existing `approve-private` command accepts `--review-plan` and an exact `--group` only after the user reviews the listed items. Creating a plan changes no graph. No approvals were executed during this refinement.

## Frozen evaluation

The parent locked a new 12-conversation synthetic export before a fresh Astra agent received only four allowlisted inputs. The read boundary was procedural, not an OS sandbox. The output was frozen and hashed before grading. Raw outputs and runtime receipts stay local and ignored.

- Expected lock SHA256: `405792cdd91421b07055903b7d2c9e84d2357ba1f4a78c0df4c73f4c53b426ae`
- Output SHA256: `599b52f5551fd38040c73d53cccd3a5b55f16cef6519124b6047502e0042f943`
- Freeze manifest SHA256: `bb75a91b5a7a903fe68451bb9fc94803907a42874a1d17fe14793a00769c36ab`
- 14/14 material source occurrences; 12 distinct proposals after two duplicate occurrences; seven high, five medium. Three separate evidence records, no unnecessary review items or uncertainty flags. One incidental acknowledgment remains only in raw source, as permitted.
- Precision and recall: 100% for material claims, classifications, speakers, subjects, entity identities, decision ownership, commitments, deadline fields, relationships, duplicate links, changes, priority, durability and numeric constraints.
- Zero conflict/uncertainty examples were required in this focused holdout: precision/recall are N/A, not proof of positive conflict detection. Earlier suites retain those cases.
- Synthetic scaling: 58.3 recommended items per 100 conversations, 100 including expanded material items. This small, parent-authored scenario mix does not prove the target for typical real exports. It must not be compared as a controlled reduction against a different earlier sample.

The synthetic readiness gate passed. No model API quality claim follows from an Astra Codex run. All proposals still need manual review; real-chat access and any later external model trial require separate authorization.
