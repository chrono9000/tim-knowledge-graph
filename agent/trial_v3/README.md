# Step 6 zero-API export realism trial

This version fixes concrete offers of help as recommendations and tests discovery from synthetic ChatGPT export records without supplied entity IDs or speaker mappings. The production offline classifier also receives the bounded offer rule and regression tests. Previous v1/v2 prompts, expected answers and frozen results remain unchanged.

`synthetic-export.json` is an explicitly synthetic supported ChatGPT export: a top-level list with conversation mapping, parent/children/current_node, message IDs, ordinary author.role/name/metadata, text content parts and Unix timestamps. The existing validate_export reader accepts all six conversations. No identity is supplied through author.name. Sources contain self-introductions, names, aliases and timezone statements as ordinary language.

`contract.py` specifies and validates discovered entities, source spans, claims, recommendation outcome, duplicateOf and supersedes proposal links, controlled relationships and field-level flags. It has no network transport or graph merger. This experimental output is not automatically convertible to approved production graph records. Type/span checks do not prove semantic entailment; source review remains necessary.

`lock.json` records input, expected answer, prompt, schema, contract and criterion hashes before the fresh extraction. `evaluate.py` accepts only the frozen output manifest hash, writes a grading receipt before loading gold, checks locked hashes, and reports unadjusted precision/recall. No answers or output are repaired after scoring.

Result: 30/30 expected claims captured, plus one source-supported context statement not included in the locked useful-review list. All scored dimensions meet 95% precision and recall, but the strict zero-unnecessary-review gate fails by one item. No conditional commit/push or CI deployment action is warranted under this result. Dedicated API access, publishing, scheduling and Step 7 remain inactive.

Limits: six parent-authored conversations with repeated explicitly irrelevant educational filler, 222 messages and about 9,200 words. This is not a representative sample of real exports. Alias introductions are explicit. General he/she referents are preserved in evidence rather than a dedicated actor/recipient field, so entity reference scores do not establish full pronoun normalization. The output uses discovered canonical IDs; duplicate linking and supersession are review proposals, never executed graph merges.
