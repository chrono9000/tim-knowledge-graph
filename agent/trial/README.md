# Current authorization

API access and billing are disabled by user instruction. The earlier Luna authorization below is historical and superseded. The live transport has been removed. A zero-API blind Astra evaluation is pending a fresh extraction context.

# Synthetic extraction trial — before Step 7

Status: synthetic-only Luna execution authorized by the user; awaiting a local API key. Scheduling remains inactive. The original proposal below is historical: its GPT-5.4 mini model and prices are superseded by the approved Luna configuration described here.

## Approved Luna run

Use `python -m agent.trial.run_luna` with `OPENAI_API_KEY` in the process environment. Requested identifier is `gpt-5.6-luna`; no separately dated snapshot was listed in the retrieved official model page. Returned model identity is recorded and must match. At $0.20/million input, $0.02/million cached input and $1.20/million output, the maximum reserved cost is $0.0112 per call, $0.1792 for 16 calls, within the $1 authorization. No automatic fallback to Terra or Sol.

The runner pins the pre-existing input, gold-answer and prompt fingerprints, reads only the synthetic fixture projection for requests, and saves requests, raw responses, model/usage receipts, assessments and numbered proposals under ignored `data/private/ai-trial/luna/`. It reserves the maximum cost durably before sending, uses a permanent exclusive marker to prevent retries or concurrent runs, fixes the HTTPS destination, prohibits redirects/proxies/tools, and stops on schema, validation, cost or transport errors. Failed/uncertain calls retain their reservations. Never delete the marker to retry without investigating the prior outcome. A missing credential stops before any call. Unit test doubles are not AI performance evidence.

Actual token cost is calculated from API usage including cache discounts and reported separately from reservations; invoice settlement is not independently queried. Uncertain failures may leave actual cost unknown. Raw successful HTTP responses are saved before parsing; a timeout, oversized body or HTTP error may leave no complete raw response. Such failures stop the trial.

Additional diagnostics count incorrect claim candidates, incomplete classifications, subject-resolution errors, deadline-field errors, and correct/missed/spurious conflict flags. Missed claims with expected subject/deadline fields count as omissions in those dimensions. Counts may overlap; semantic correctness needs human adjudication, including extra claims. No expected answers were changed. Passing the small synthetic suite cannot establish general real-chat reliability.

Validated locally: 81 tests. Current execution blocker: no `OPENAI_API_KEY` in the inspected process or Windows user environment. No external requests or model results yet. See [official Luna documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

Run `python -m agent.trial.evaluate` to reproduce the original deterministic extractor baseline and prepare requests. Outputs default to ignored `data/private/ai-trial/`. This module has no network transport, credential lookup, approval, merge, or publish command. Trial interpretations are isolated from the production validator, which currently rejects contextual interpretations not recognized by its sentence rules.

The adapter preserves the existing `extract(messages)` interface with extended claims for semantic attributes, evidence spans and uncertainty. `review_recorded` validates a supplied response and produces numbered private proposals; its caller-declared origin is not proof a model ran. Test doubles verify plumbing only. A real run needs response receipts, model identity, usage, request hashes and manual adjudication.

## Evaluation

`inputs.json` contains 8 development and 8 held-out synthetic cases. `expected.json` is consumed only by the evaluator. Requests include only the generic prompt, context and messages. Held-out means excluded from examples and prompt tuning; these cases were authored during the same preparation, not independently blinded. Freeze the input, expected-answer and prompt hashes before the first external run. Do not revise held-out expectations to fit results. Any genuine annotation correction requires a versioned explanation and rerun report.

Compare development and held-out results separately. Exact source span and message match identify claims; category, epistemic status and structured attributes measure semantic coverage. Report wholly missed claims, missed or misrepresented items, classification errors, unsupported claim candidates, incorrect/missing relationships and correct/spurious uncertainty flags. A claim counts once as incomplete even if multiple fields are wrong; classification errors count fields. Flag counts are separate from semantic coverage. Review candidate errors manually: legitimate extra claims and alternate exact spans can be overcounted. No automatic score is a truth oracle.

Inspect raw and normalized relationships. Normalization removes exact duplicates and redundant owner-to-statement edges when the same owner-to-entity relationship is present. It does not establish that surviving relationships are correct. Source quoting likewise does not establish that an interpretation is supported.

Proposed readiness criteria: zero adjudicated unsupported claims or incorrect relationships; at least 90% complete expected items and 90% required uncertainty flags, with every extra flag reviewed. Report exact counts on this small suite. Passing is only a reason to request a small redacted real-chat trial, not general reliability evidence. All trial proposals remain ineligible for production approval until reviewed conversion is implemented; never auto-merge.

## Activation boundary

Proposed model: `gpt-5.4-mini-2026-03-17` via Responses, low reasoning, strict structured output, `store=false`, `background=false`, no tools. One request per case, at most 16, no automatic retries. Reserve 20,000 input and 6,000 output tokens per call. At published $0.75/$4.50 per million input/output tokens, reserve $0.042 per call, $0.672 total within a $1 cap. Input UTF-8 bytes plus framing allowance conservatively bound the prepared payload; verify actual provider token accounting before execution.

The current budget class is an offline envelope check, not an enforceable account spending limit. Before any external execution, implement durable reservation before each call, consume reservations on failure/unknown outcome, stop at the call or dollar cap, pin approved request hashes, and validate no unexpected request fields or tools. Do not retry uncertain calls or silently switch model/prices. Record actual usage. Price/access changes require rechecking the proposal. Account budget alerts alone are not hard caps.

Only synthetic messages, synthetic speaker mappings, source dates/timezones, generic extraction instructions and output schema may leave the machine. No gold answers, real chats, private graph, other proposals, credentials in payloads, or repository content. User approval must precede transport activation. Then configure a dedicated project API key with minimum required permissions in a local environment or OS secret store; never in chat, tracked files or request JSON. Account access and billing are unverified. ChatGPT subscription billing does not fund API calls.

OpenAI API data is not used for training by default unless opted in. `store=false` does not mean zero retention: abuse monitoring logs may remain up to 30 days with stated exceptions; applicable extended prompt caches may persist up to 24 hours. Zero Data Retention is not assumed. Local synthetic outputs remain until manually removed.

Sources: [model and pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [data controls and retention](https://developers.openai.com/api/docs/guides/your-data), [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

Smallest real-chat setup after the synthetic trial: a permitted local export, reliable speaker identity mapping and source timestamps/timezones; a small user-selected redacted sample; explicit approval of what may leave the machine; and reviewed conversion into the existing manual proposal workflow. Address observed extraction failures first. Scheduling and Step 7 are unnecessary for this evaluation.
