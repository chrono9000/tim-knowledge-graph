# Source access and inactive AI design

Verified against official OpenAI documentation on 2026-09-07. Recheck account eligibility and documentation before activation.

## Supported source access

- [ChatGPT exports](https://help.openai.com/en/articles/7260999-how-do-i-export-my-chatgpt-history): user-requested, asynchronous downloads, with workspace-dependent eligibility. Managed workspaces can require administrator assistance.
- [Exported conversation transfer](https://help.openai.com/en/articles/9106926): conversation JSON may be split into numbered files. Step 6 supports extracted arrays individually; its ZIP reader retains the Step 5 single-file limit.
- [ChatGPT Projects](https://help.openai.com/en/articles/10169521-projects-in-chatgpt): organizes chats, files and instructions inside ChatGPT. This does not establish a general personal-project synchronization API or guarantee export coverage of every asset.
- [Conversations API](https://developers.openai.com/api/reference/python/resources/conversations/methods/create): creates conversation state for API applications. It is not documented as a consumer ChatGPT-history retrieval endpoint. No technically available consumer history/project connector has been established for this application.
- [OpenAI Compliance Platform](https://help.openai.com/en/articles/9261474-openai-compliance-platform-for-enterprise-customers): organization-managed compliance access exists for eligible Enterprise/Edu workspaces with workspace-scoped Admin permissions and appropriate owner authorization. This is a conditional future option, not an activated consumer connector.

The source decision is therefore manually placed private exports/notes. This application never accesses ChatGPT sessions or uses Codex task-reading tools as a production synchronization backend.

## Provider interface and trust boundary

`Extractor.extract(messages)` returns bounded `Claim` objects. `Message` includes ID, role, timestamp and text used in memory. A claim identifies a verbatim span, category, epistemic type, confidence and explicitly supported ownership. The contract covers people, roles, entities, projects, systems, decisions, preferences, constraints, responsibilities, commitments, questions, risks, policies, supersession and relationships.

`DeterministicExtractor` is the offline default. `InactiveAIAdapter` accepts mock responses for synthetic contract tests; without a mock it fails closed. There is no network transport, credential lookup or activation switch. This is a tested adapter contract and activation design, not a production LLM extractor.

Provider output passes evidence and role/classification validation before FEOS evaluation. It cannot assign authority, turn prose into independently verified facts, invent ownership, write files, approve or publish through the interface. Source instructions are data. A future model must return constrained JSON with no tools, subject to deterministic validation and Tim's review.

Staging retains bounded quotes, conversation/message references, file and normalized-content hashes, timestamps, ownership, authority/confidence, first/last seen and provider version. Graph nodes contain selected statements, not full conversation payloads. Unverified user statements use the existing graph's `assumption` type while staging preserves their distinction. Repetition/private approval does not silently upgrade source authority. The 22-rule FEOS catalog is unchanged.

The conservative offline rules can miss complex entities, multilingual nuances, implicit contradictions and pronoun ownership. Overlap can yield false-positive conflict warnings. A missing current branch is flagged. This is proposal assistance requiring human review, not complete semantic understanding.

## Separate activation decisions

Before any paid or external processing, Tim must approve:

1. Provider and exact production model. The model building Step 6 is not the production-model choice.
2. Allowed data scope, including whether sensitive snippets may leave the computer. Local viewing approval does not authorize cloud extraction.
3. Spending limit, input/output token caps, retry/timeout limits and budget-exhaustion behavior. Estimate costs from the selected model's current official rates before approval.
4. Dedicated API project/key and least-privilege local secret storage outside Git. Never put keys in source, logs, fixtures or conversations. ChatGPT subscription access does not authorize API billing.
5. Retention/data controls. OpenAI's [API data controls](https://developers.openai.com/api/docs/guides/your-data) describe training defaults, endpoint-dependent application storage, abuse-monitoring retention and eligibility for modified/zero-retention controls. `store: false` alone is not a blanket zero-retention guarantee. Verify the chosen endpoint, model and organization before sending data.
6. Actual transport implementation and synthetic testing of schema enforcement, budget limits, injection defenses and redaction, followed by a separately approved limited private pilot.

Send only the minimum necessary message window, disable unnecessary storage/tools, explicitly select a model, and log counts/operational IDs instead of prompts or responses. Without transfer approval, remain offline. Step 6 adds no credentials, activates no external extraction, and makes no billable extraction request.

## Safety design and boundaries

One OS lock next to the public graph serializes supported CLI mutations, including alternate private paths. Numbers use stable queue order. Approval/publication validates before writes. A durable private journal holds before/after snapshots; atomic file replacement precedes completion/audit entries. Staging can replay automatically; interrupted graph writes need explicit recovery. Recovery grants no fresh approval. Audit and snapshot entries are retained.

Raw sources, journals, manifests, approvals, proposals, private graphs and logs are ignored by Git; Pages retains its five-file allowlist. Earlier steps' low-level `python -m agent.ingest` tool is separate: never use or schedule it for this private workflow. Only `python -m agent` commands participate in Step 6 controls. Filesystem administrators and Python plug-in code remain trusted; this interface is not an OS sandbox.
