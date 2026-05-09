# Agent + Copilot Architecture

**Status:** active design — covers sub-projects A (order extractor overhaul) and
B (sales copilot). Both share schemas, observability, and verification surfaces;
this spec is the single source of truth for both.

**Author:** ai (claude-opus-4-7) + oliver, brainstormed 2026-05-08 against a
live deploy at <https://waggle.fly.dev>.

**Principle:** no half-measures. Build everything correctly upfront — the
project must be complete before being shown.

---

## 1. Goals

1. **Honest extraction.** Every field the LLM produces is grounded in
   verbatim quotes from the source text and tagged with a self-rated
   confidence score. Hallucinations are visibly impossible because the
   evidence either appears in the input or it doesn't.
2. **Conversational copilot at parity with the email agent.** A multi-turn
   chat interface with intent routing, account/customer awareness, tool
   calls (lookup + create), audio dictation for new customer reports, and
   in-flow order creation.
3. **No exponential failure modes.** Conversation history, tool calls, and
   token budgets are bounded by design. The agent cannot crash itself by
   running for too long.
4. **Empirically chosen models.** A reproducible benchmark across
   `gpt-4o-mini`, `gpt-5.4-*`, `gpt-5.5-*` (and any nano variants that
   exist as of build time) decides which model serves which stage. No
   guessing.
5. **Backend-only quality signals.** Confidence scores, model metrics, and
   audit trails live in the DB and admin views, never in the customer-
   facing UI.
6. **Two-layer verification.** Every user-facing change passes both the
   subagent walk and the Playwright + pytest E2E suite per
   `.claude/rules/ui-verification.md`.

---

## 2. Inspiration & deltas

Some prior dual-agent CRM systems (email-extraction agent + sales copilot)
ship with patterns worth adopting:

- **OpenAI native structured outputs** (`client.beta.chat.completions.parse(response_format=Schema)`) instead of LangChain's `with_structured_output`.
- **Per-step confidence** baked into each schema (not a single global score at the end).
- **Two-tier model usage** — fast model for routing, smarter model for complex extraction.
- **Single canonical result object** per agent run, persisted with metrics.
- **`extraction_concerns: list[str]`** that captures the LLM's own uncertainties for human review.
- **Per-line-item `notes`** for granular ambiguity flags.
- **No-Guess Policy** — flag for human review rather than fabricate.
- **Reference-date in prompt** for relative date resolution.

**Waggle extends and improves on these** in the following ways:

| Concern | Some prior implementations | Waggle |
|---|---|---|
| Hallucination grounding | `confidence` only at top level; no verbatim citation | `evidence: list[str]` (verbatim quotes) on **every** extracted field; backend audits that quotes appear literally in source |
| Tool-call accumulation | Truncation by message count → exponential growth then crash | Tool-call-aware truncation: budget `total_tool_calls_in_history`; summarize older turns into text notes |
| Token overflow | Budget calculator existed but never wired up | Budget enforcement called on every chat turn as a hard invariant; deploy fails if not exercised |
| Entity resolution | Hardcoded names in resolver code (`if "lakewood" in name:`) | Pure data-driven indexing from DB rows, populated at startup |
| Prompt source of truth | DB-backed, with seed source in repo (drift incidents possible) | Prompts in code, deployed via PR. No live-tune-via-DB convenience |
| Stale code | Multiple `_OLD`, `_v2` versions accumulate | Single source of truth per function; deletes happen |
| Conversation history | Plain `(question, answer)` tuples | Pydantic `ChatTurn` with metrics, evidence, tool calls |
| Model selection | Pinned to one model in code | Empirical benchmark drives per-stage assignment |

---

## 3. Schemas

All schemas use Pydantic v2 with `from __future__ import annotations`.

### 3.1 Base mixin — evidence + confidence

```python
class Grounded(BaseModel):
    """Mixin for any LLM-filled schema. Forces evidence + self-confidence.

    `evidence` is a list of verbatim quotes from the source text that justify
    the populated fields of the containing model. The agent runner audits
    that each quote appears literally in the source string; quotes that
    don't are flagged as hallucinations and the extraction is rejected.

    `confidence` is the LLM's self-rated confidence in the extraction
    (0.0–1.0). Backend-only — never rendered in user-facing templates.
    Used for monitoring, low-confidence flagging, and model-comparison
    benchmarks.
    """
    model_config = ConfigDict(frozen=True)

    evidence: list[str] = Field(
        default_factory=list,
        description="Verbatim quotes from the source text that justify this extraction.",
    )
    confidence: float = Field(
        ...,
        ge=0.0, le=1.0,
        description="Self-rated confidence in this extraction (0.0–1.0).",
    )
```

Every schema the LLM fills inherits from `Grounded`.

### 3.2 Order-extractor schemas (revised)

`agents/schemas.py`:

- `IntentClassification(Grounded)` — `intent: Literal['order_request','inquiry','complaint','other']`, `reasoning: str`
- `CustomerReference(Grounded)` — `mentioned_name: str`, `requester_is_customer: bool`
- `RequestedLineItem(Grounded)` — `description: str`, `quantity: int`, `mentioned_sku: str | None`, `mentioned_unit_price_usd: Decimal | None`
- `ResolvedLineItem` — wraps a `RequestedLineItem` with `sku`, `match_method`, `unit_price_usd`. Inherits `Grounded` (the LLM-fallback resolver step also produces evidence).
- `ExtractionConcerns` — list of judgment-call notes the agent surfaces for the salesperson.
- `OrderDraft` — composed of all above + `extraction_concerns: list[str]`. Plus `overall_confidence: float` derived from per-step confidences (e.g., min, weighted mean — TBD by benchmark).

### 3.3 Conversation schemas (new, for copilot)

`agents/conversation_schemas.py`:

- `ConversationTurnRole = Literal['user','assistant','system','tool']`
- `ToolCallRecord` — `tool_name`, `arguments: dict`, `result_excerpt: str` (capped at 4K chars), `latency_ms`, `tokens`
- `ChatTurn(BaseModel)` — `id: UUID`, `role`, `text: str`, `tool_calls: list[ToolCallRecord]`, `evidence_used: list[str]`, `model: str`, `tokens`, `cost_usd`, `latency_ms`, `created_at: datetime`
- `Conversation(BaseModel)` — `id: UUID`, `salesperson_id: str | None` (None = public demo), `turns: list[ChatTurn]`, `active_account_id: UUID | None`, `created_at`, `updated_at`
- `IntentRoute(Grounded)` — `intent: Literal['query','log_customer_report','create_order','command','unknown']`, `target_account_hint: str | None`
- `CopilotResponse` — text answer + `citations: list[Citation]` (pointers to retrieved chunks / DB rows that grounded the answer)

### 3.4 Persistence

`db/models.py` gets two new tables (alembic migration):
- `conversations` — id, salesperson_id (nullable), active_account_id, created_at, updated_at, model_used
- `chat_turns` — id, conversation_id (FK), role, text, tool_calls (JSONB), evidence_used (JSONB), model, tokens, cost_usd, latency_ms, created_at

Indexed on conversation_id + created_at for replay. JSONB for tool_calls and evidence so we don't multiply tables.

---

## 4. Order extractor — architecture

**Decision: keep the per-node LangGraph design.** The diagnostic value of seeing
which stage failed (intent / customer / items / SKUs) is worth the 4–5 LLM
calls per email — for a portfolio-quality demo the marginal cost is
negligible and the explainability is the entire point.

### 4.1 Nodes (post-redesign)

1. `classify_intent` → `IntentClassification`
2. `extract_customer` → `CustomerReference`
3. `match_customer` → `CustomerMatch` (deterministic fuzzy match against DB; not LLM-filled, but adds confidence based on score band)
4. `extract_line_items` → `list[RequestedLineItem]`
5. `resolve_skus` → `list[ResolvedLineItem]`
   - First: deterministic fuzzy match
   - Fallback: LLM-resolution call for unmatched descriptions (also produces `Grounded`-shaped output, evidence = the description text)
6. `assemble_draft` → `OrderDraft`

Failure at any node short-circuits to END with an `ExtractionFailure` carrying
the stage, reason, partial state, and a calm classification of *why* it's
not user-visible-as-error (see §7 UX).

### 4.2 Prompt design — simple + schema-driven

Prompts are short, declarative, and delegate constraint enforcement to the
schema. The pattern:

```
You are <role>. Given <input>, return <Schema>.

<2-3 lines of guardrails — never invent X, prefer Y, etc>

For evidence: quote the exact text from the source that justifies your
extraction. If you cannot quote evidence, return low confidence.

Source text:
<input>
```

No chain-of-thought scaffolding, no few-shot examples unless variance
testing shows they're needed. Trust the schema.

### 4.3 SKU resolver fallback — anti-hallucination guard

When fuzzy match returns no candidate above threshold for a line item, the
LLM is given the description + the entire SKU catalog (typically <100 SKUs
post-truncation) and asked to pick one with `evidence` (which catalog row's
text triggered the choice) and `confidence`. If confidence < 0.6, the item
is left unresolved and added to `extraction_concerns`. **No silent guesses.**

---

## 5. Sales copilot — architecture

### 5.1 Chat flow

```
user input (text or transcribed audio)
    ↓
intent_router (single LLM call, fast model)
    ↓
┌──────┬────────────────────┬────────────────┬──────────┐
QUERY  LOG_CUSTOMER_REPORT  CREATE_ORDER     COMMAND
↓      ↓                    ↓                ↓
account_resolver            structured       guarded
    ↓                       extraction       command
context_builder             (Grounded)       (validation
    ↓                       ↓                 + audit)
RAG retrieval               DB write
    ↓                       (atomic)
LLM (smart model) ← tools (MCP-backed lookup)
    ↓
CopilotResponse with citations
```

### 5.2 Components

- **`agents/copilot/intent_router.py`** — single LLM call, returns `IntentRoute`. Uses fast model.
- **`agents/copilot/account_resolver.py`** — pure deterministic indexing of DB rows on startup. Tokens + phrases → customer_id list. No hardcoded names; refreshed when customers change.
- **`agents/copilot/context_builder.py`** — assembles RAG context from pgvector hits + recent customer reports + open orders. Token-budget aware.
- **`agents/copilot/orchestrator.py`** — single chat turn: route → resolve → assemble → call → record. Returns `ChatTurn` + updated `Conversation`.
- **`agents/copilot/tools.py`** — MCP-backed tools the LLM can call:
  - `lookup_customer(name_or_id)`
  - `search_customer_reports(filters)` — with filters per existing retrieval module
  - `get_open_orders(customer_id)`
  - `draft_order(customer_id, line_items)` — invokes order_extractor pipeline path
  - `log_customer_report(customer_id, transcript)` — structured extraction + DB write
- **`agents/copilot/budget.py`** — token + tool-call budget. Called every turn. Truncates older turns' tool calls into text summaries when budget is exceeded.

### 5.3 Conversation persistence

Every turn writes a `chat_turns` row + updates the `conversations` row. On
load, the orchestrator hydrates the last N turns (capped by token budget,
not message count). Tool call results in older turns are summarized to
text before being injected into LLM context.

### 5.4 Audio input

- Frontend: HTMX form with `<input type="file" accept="audio/*">` + browser MediaRecorder for live capture.
- Backend: `POST /api/copilot/transcribe` accepts an audio blob, sends to OpenAI Whisper API, returns transcript text.
- The transcript flows through the same chat orchestrator as typed input — STT is just a preprocessor, not a separate path.

### 5.5 Customer report logging via dictation

User dictates *"Visited St. Agnes today, mites at 3.5%, queens look healthy, scheduled for follow-up next month."*

Flow:
1. STT → transcript
2. Intent router → `log_customer_report`
3. Structured extraction with `Grounded` `CustomerReportDraft` schema (visit_date, customer_id resolved by account_resolver, varroa_pct, observations, action_items, evidence per field, confidence)
4. Render draft to user with "Save" / "Edit" / "Cancel" buttons
5. On save: alembic-managed `customer_reports` row inserted

If `confidence < 0.7` or `varroa_pct` is parsed but evidence is missing,
the draft surfaces with the offending fields in a "needs review" state.

### 5.6 Order creation via copilot

User says *"Create an order for St. Agnes for 40 hives, March delivery."*

Flow: same shape — intent router → `create_order` → structured extraction
(reusing `OrderDraft` schema and resolver tools from the email path) → user
review → on save, `orders` + `order_items` rows + ERP sync trigger.

---

## 6. Model benchmark

### 6.1 Candidate set

| Model | Test | Notes |
|---|---|---|
| `gpt-4o-mini` | yes | baseline; widely available |
| `gpt-5.4-mini` | yes | current default; reference for parity check |
| `gpt-5.4-nano` | if exists | probe API; skip on `model_not_found` |
| `gpt-5.5` | yes | likely candidate for hardest stages |
| `gpt-5.5-mini` | if exists | probe API |
| `gpt-5.5-nano` | if exists | probe API |

The harness runs each candidate against each stage, gracefully skipping
404 / model-not-found responses, and records `(model, stage, latency,
tokens, cost, output)`.

### 6.2 Ground truth

Built from `seed/fixtures/*.inbox.json` plus the `seed/generate.py`-generated
DB state we already have. We know:
- Which customer each fixture email is "from"
- Which intent the generator labeled
- Which line items were intended

For each fixture, derive:
- `expected_intent: Literal['order_request','inquiry',...]`
- `expected_customer_id: UUID | None`
- `expected_line_items: list[ExpectedItem]` (sku, qty)

Stored in `tests/ground_truth.json` (committed).

### 6.3 Metrics

Per (model, stage):
- **Latency** (p50, p95) in milliseconds
- **Cost** in dollars per call (computed from `tokens × model_price`)
- **Quality**:
  - Intent: classification accuracy
  - Customer: resolution accuracy (matched correct customer_id?)
  - Line items: precision + recall on SKU set, MAE on quantities
  - Evidence: do quotes appear literally in source? (string-membership test)
  - Confidence calibration: high-confidence-and-correct rate vs. high-confidence-and-wrong rate

### 6.4 Output

`scripts/benchmark_models.py` produces:
- `docs/benchmarks/2026-05-08-extract-models.md` — narrative summary + table
- `docs/benchmarks/2026-05-08-extract-models.json` — raw per-call data (for re-analysis)

### 6.5 Decision

The benchmark output drives a per-stage assignment in `app/config.py`:

```python
class Settings(BaseSettings):
    extract_intent_model: str = "<benchmark winner for intent>"
    extract_customer_model: str = "<winner for customer>"
    extract_items_model: str = "<winner for line items>"
    extract_resolver_fallback_model: str = "<winner for SKU fallback>"
    copilot_router_model: str = "<winner for copilot intent>"
    copilot_chat_model: str = "<winner for copilot main turn>"
```

No single `openai_model` field anymore — different stages have different
needs. The benchmark report cites the choice for each.

---

## 7. UX / templates

### 7.1 Extract path (current pages)

**`partials/agent_failure.html` — split into two visual states:**

- **Informational state** (when `failure.stage == "classify_intent"` and `failure.intent != "order_request"`): calm card. Title: *"This message isn't an order request"*. Body: *"The agent only drafts orders from explicit purchase requests. This looks like a/an {intent}."* No JSON dump, no internal references, no red.
- **Error state** (every other failure stage): restrained warning card. Title: *"We couldn't draft an order from this email"*. Body: a one-sentence reason in plain language ("Couldn't identify the customer", "Couldn't find any items in the catalog matching what was requested"). No JSON dump. No `.claude/rules/...`.

**`partials/agent_draft.html` — strip dev language, add evidence affordance.**

- Remove the *"Draft is not persisted — confirm-and-persist is still open work. To drive an order through the full FSM (DRAFT → … → SYNCED_TO_ERP) end-to-end, run `docker compose run --rm seed python -m scripts.demo`"* footer entirely.
- Add a hoverable info icon next to each customer match and line item that surfaces the verbatim evidence quote ("matched by 'St. Agnes Pollination' in the email body").
- Confidence is **never** rendered. Backend logs it.

**`inbox.html` — strip *"local demo — no external services required"* footer.**

### 7.2 Copilot UI (new pages)

- `/copilot` — chat surface. Server-rendered, HTMX-driven. Left rail: conversation list. Center: messages. Right rail: live "context preview" showing which customer / reports the current turn drew from.
- `/copilot/<conversation_id>` — load existing conversation.
- `<input type="text">` + a microphone button for audio. The mic uses the browser's MediaRecorder; on stop, the audio blob is `POST`ed to `/api/copilot/transcribe`.
- After each assistant turn, citation chips appear under the response — clickable, jump to the source customer report or order.

### 7.3 Domain explainers (sub-project C, but planned here)

Inline tooltip definitions for terms like "varroa", "queen", "pollination contract" — implemented as a shared Jinja partial plus a small JSON glossary file. Out of scope for A; locked in here so the data model can carry a `glossary_term` field if a customer report references one explicitly.

---

## 8. Observability

- **Per-turn metrics** stored on `chat_turns`: model, tokens (in/out), cost_usd, latency_ms, tool_call_count
- **Per-extraction metrics** stored alongside `OrderDraft` persistence: stage, model, latency, tokens, cost, confidence
- **Admin dashboard** at `/admin/agent-metrics` (basic-auth gated when `WAGGLE_USER`/`WAGGLE_PASS` are set; otherwise dev-only) showing per-stage cost, latency, error rate, confidence distribution.
- **Cost cap**: settings field `monthly_llm_cost_cap_usd`; every LLM call checks the running monthly cost before firing. If cap reached, every subsequent extraction returns a hardcoded "service paused" failure (loud, not silent).

---

## 9. Verification

Per `.claude/rules/ui-verification.md`, every change in this spec passes
both layers before "done":

### 9.1 Layer 1 — per-clickable subagent walks

For each affected page, dispatch one subagent per interactive element:
- `/inbox`: every Extract button (10 fixture emails sampled), all nav links
- `/customers`, `/orders`, `/customer-reports`, `/dashboard`, `/erp-log`: nav, every row link, every filter
- `/copilot`: text input, mic button, send button, every conversation list item, citation chips

Each subagent uses Playwright MCP, performs the click, observes the
result, returns structured pass/fail with screenshot evidence.

### 9.2 Layer 2 — Playwright + pytest E2E

New test files:
- `tests/e2e/test_extract_path.py` — happy + edge cases for /inbox/extract
- `tests/e2e/test_copilot_chat.py` — multi-turn conversations, tool calls, budget enforcement
- `tests/e2e/test_copilot_audio.py` — audio upload + transcription
- `tests/e2e/test_copilot_log_report.py` — dictation → customer report
- `tests/e2e/test_copilot_create_order.py` — copilot → order creation
- `tests/e2e/test_evidence_grounding.py` — verifies evidence quotes appear literally in source for every extraction (anti-hallucination invariant)

All tests run against real pgvector + real LLM (skipped when no key per `e2e-testing.md`).

---

## 10. Implementation sequence

### Phase A — order extractor overhaul

1. **Schemas** (`agents/schemas.py`): add `Grounded` mixin, retrofit all extraction models.
2. **Prompts**: rewrite each node's prompt with evidence requirement; remove anti-examples that turned out unnecessary post-schema.
3. **SKU resolver fallback**: implement LLM-fallback path for unmatched descriptions.
4. **Order extractor nodes**: update to fill new schemas, propagate evidence + confidence into `OrderDraft`.
5. **Templates**: rewrite `agent_failure.html`, strip `agent_draft.html` and `inbox.html` dev language, add evidence affordance.
6. **Benchmark harness** (`scripts/benchmark_models.py`): build, run, record.
7. **Settings**: per-stage model assignment from benchmark output.
8. **Tests**: new + updated E2E coverage.
9. **Verification**: Layer 1 + Layer 2.
10. **Deploy + verify on prod**.

### Phase B — sales copilot

11. **Conversation schemas + DB tables** + alembic migration.
12. **Account resolver** (data-driven from DB, no hardcodes).
13. **Intent router** (single LLM call, fast model).
14. **Context builder** (RAG retrieval, token-budget aware).
15. **Tools** (lookup, search, draft_order, log_customer_report).
16. **Orchestrator** (single turn).
17. **Budget enforcement** (token + tool-call truncation).
18. **Audio transcription endpoint** (Whisper API).
19. **Templates** for `/copilot`.
20. **Tests**: full coverage per §9.
21. **Verification**: Layer 1 + Layer 2.
22. **Deploy + verify on prod**.

---

## 11. Out of scope (this spec)

- Multi-tenant auth (waggle is single-tenant for now)
- Real ERP integration (the JSON-line mock stays)
- Mobile-native app
- Streaming responses (server-sent events) — synchronous turn responses for now; HTMX polling if a turn is slow
- Multi-language (English only)
- Sub-project C (client polish + filters + domain explainers) — separate spec follows after B

---

## 12. Open questions

None blocking. Design is approvable as-is.

---

## 13. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Benchmark cost explodes | Low | Cap at 200 LLM calls per benchmark run; fixture set is bounded |
| Whisper API latency makes audio feel slow | Medium | Show a recording-then-processing UI state; cap audio at 30s |
| Evidence-grounding rejects legitimate extractions | Medium | If quote isn't a literal substring but is fuzzy-equal (case/whitespace), accept; log the discrepancy for review |
| Token budget triggers mid-conversation | High by design | Show a calm "summarized older context" indicator in the UI |
| Copilot tool-call cycles on a loop | Medium | Hard cap of N=10 tool calls per turn; raise after that |
