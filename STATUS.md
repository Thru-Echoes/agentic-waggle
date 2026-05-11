# Waggle — Status

Tracks what works, what's in-flight, and what's next.

## Scope

**Tier 2** — MVP CRUD + LangGraph order-extraction agent + both MCP servers + inbox-fixture-driven demo + Jinja/HTMX frontend. Not React (keeps the server-render story clean). Not Celery (no async-required work in the demo path).

## Complete

### Seed generators (16) — `seed/`
All generators own Pydantic models (`seed/models.py`) and write via raw SQL with `text()`. Fail-loud throughout.
- [x] `regions.py` → 6 regions (territories). Duplicate code raises IntegrityError.
- [x] `apiarists.py` → 16 users (12 field + 3 ops + 1 admin). Unknown region raises LookupError.
- [x] `customers.py` → 40 customers with deliberate edge-case names.
- [x] `sites.py` → ~75 customer sites. Missing customer raises LookupError.
- [x] `products.py` → 60 SKUs across 6 categories.
- [x] `services.py` → 4 pollination SKUs.
- [x] `regulated_items.py` → **exactly the curated list** (no padding); drift raises LookupError.
- [x] `red_flags.py` / `approval_rules.py` → 10 each; duplicate name raises.
- [x] `inspections.py` → 40 apiary inspection reports.
- [x] `emails.py` → 80 synthetic inbound emails (45 clear / 20 ambiguous / 8 non-order / 7 complex); sized to cover the 65 orders whose state requires a `source_email_id`.
- [x] `orders.py` → 150 orders across every FSM state; insufficient emails raises upfront.
- [x] `embeddings.py` → chunker + sentence-transformers (384-dim); idempotent.

### Providers (6) — `providers/` (renamed from `overrides/`)
These are the app's **first-class implementations**, not stubs overlaying real code.
- [x] `vector_search.py` — sentence-transformers + pgvector cosine search. (was `vertex_ai_stub.py`)
- [x] `storage.py` — local filesystem object store with path-traversal protection. (was `gcs_stub.py`)
- [x] `erp.py` — JSON-line "Hive-side ERP" mock; `customer_exists` requires a real DB session. (was `sage_stub.py`)
- [x] `inbox.py` — fixture-backed inbound-mail reader; missing fixture raises `InboxFixtureMissing`. (was `gmail_stub.py`)
- [x] `mailer.py` — outbound mail logged to `/sent-mail/` as JSON records. (was `mail_send_stub.py`)
- [x] `llm.py` — OpenAI `gpt-5.4-mini` by default (toggle `llm_provider` in `app.config.Settings` or env `LLM_PROVIDER=anthropic`). Raises `LLMConfigurationError` if the selected provider's key is missing *and* the fallback's key is missing. `resolve_provider()` is the single-source decision used by both `LLMClient` and `agents/order_extractor._build_chat_model`. (was `llm_stub.py`)

### E2E test suite (21 modules) — `tests/`
Real deps throughout: real pgvector, real sentence-transformers, real filesystem, real LLM when a key is set (explicit skip otherwise).
- [x] `conftest.py` — per-test fresh schema, Faker(seed=42), fixtures for each provider.
- [x] `schema.sql` — standalone schema, 17 tables, tight constraints; authoritative column list.
- [x] `test_seed_*.py` (13 files) — every generator tested: count, distribution, duplicate-raises, missing-FK-raises.
- [x] `test_override_*.py` (6 files) — every provider tested: happy path, missing-config, real-API forwarding (LLM).
- [x] `test_integration_full_seed.py` — runs full pipeline, asserts exact row counts across every table.

### Architecture diagrams (all 7, `waggle-` prefix) — `../diagrams/rendered/`
Rendered via Playwright from Mermaid sources in `../diagrams/source/`.
- [x] `waggle-01-system-architecture.png` — **rewritten** 2026-04-23: 6-tier Waggle stack (Browser + MCP Client → FastAPI → Agent + MCP servers → first-class providers → Postgres/pgvector + local volumes → optional LLM API). Honey-amber palette.
- [x] `waggle-02-order-pipeline-fsm.png`
- [x] `waggle-03-ingestion-pipeline-fsm.png`
- [x] `waggle-04-agent-extraction-sequence.png`
- [x] `waggle-05-data-model-er.png`
- [x] `waggle-06-rag-pipeline.png`
- [x] `waggle-07-acronyms-reference.png`

### Policies (enforced) — `.claude/rules/`
- [x] `code-quality.md` — functional style, naming, Pydantic v2, type safety.
- [x] `fail-loud.md` — no ON CONFLICT, no silent FK skips, no canned LLM, no silent empty-return.
- [x] `e2e-testing.md` — real deps, no vacuous tests, no mocks of primary deps.

## Phase B — foundation (done 2026-04-23 → 2026-04-29)

- [x] Doc + memory pivot to Tier 2.
- [x] Rename `overrides/` → `providers/`.
- [x] `docker-compose.yml` builds locally from a single `Dockerfile`.
- [x] `Dockerfile` for api / worker / seed entrypoints.
- [x] `requirements.txt` — FastAPI, LangGraph, MCP, sentence-transformers, etc.
- [x] `db/models.py` — SQLModel matching `tests/schema.sql`. Uses `Mapped[...]`
  wrappers + `_enum_col` helper with `values_callable` so SA 2.0 mapper
  resolves enum values (lowercase) instead of names. **This file is the only
  one in the project that omits `from __future__ import annotations` —
  necessary for the SA 2.0 mapper. See the docstring/comment in the file.**
- [x] `alembic/` + first migration creates pgvector + 17 tables.
- [x] `Makefile` uses local alembic, no external-dir checks.
- [x] First `make migrate && make seed` against local-alembic schema.

## Phase C — app skeleton (done 2026-04-29)

- [x] FastAPI app factory at `app/main.py`.
- [x] Routes: health, customers, orders, customer_reports, search, agent, pages.
  `response_model` on every JSON route. JSON namespaced under `/api` so the
  HTML page routes don't clash.
- [x] Jinja templates: `/`, `/inbox`, `/customers`, `/orders`, `/customer-reports`,
  `/erp-log`, plus partials for customer-report detail, agent draft, agent failure.
- [x] HTMX-driven partial updates (customer-report detail swap, agent extraction
  result swap on the inbox page).

## Phase D — agent + MCP (done 2026-04-22)

- [x] `agents/order_extractor.py` — LangGraph StateGraph: classify intent →
  extract customer reference → fuzzy-match → extract line items (LLM
  structured output) → resolve SKUs → assemble draft. Real LLM verified
  end-to-end against OpenAI.
- [x] `mcp_servers/sales_copilot_server.py` — MCP tools: customer/site lookup,
  customer-report search, email drafting. Directory is `mcp_servers/` (not `mcp/`)
  to avoid shadowing the PyPI `mcp` SDK on import resolution.
- [x] `mcp_servers/customer_reports_server.py` — MCP tools: narrative parsing,
  query by disease code / varroa threshold, fleet-health aggregation.

## Phase E — demo + tests + diagram

- [x] `scripts/demo.py` — 5-minute orchestrator (email → agent → FSM → ERP →
  Copilot follow-up).
- [x] E2E tests for app + agent + MCP layers — full suite **187 passed, 0
  failed, 8 skipped** as of 2026-04-29 against real pgvector +
  sentence-transformers + OpenAI.
- [x] Rewrite `01-system-architecture.html` + re-render via Playwright (2026-04-23).

## Open / next

- [ ] Initial git commit + push to `Thru-Echoes/agentic-waggle` (remote
  configured; no commits yet).
- [ ] Record a 60-90s screen capture of the demo for the portfolio site.
- [ ] Optional: hosted live demo (Fly.io or similar). Requires a budget cap
  on the LLM key and a magic-link / basic-auth gate.
- [ ] Phase E++: a confirm-and-persist UI for the **inbox** agent draft
  (the copilot flow at `/copilot` now persists; `/inbox` still doesn't
  write to `orders`).

## Phase F — sales copilot frontend (done 2026-05-10)

Sub-project B: the chat-style sales copilot end-to-end. Backend
orchestrator + persistence (commit e8190df) → user-facing UI + route
layer + confirm-and-persist drafts + two-layer UI verification.

- [x] `agents/copilot/conversation_schemas.py` — extended
  `CopilotResponse` with `pending_order_draft` / `pending_report_draft`
  / `resolved_customer_id` so the route can stash drafts on the
  assistant `ChatTurn` for later confirmation.
- [x] `agents/copilot/orchestrator.py` — emits the new draft fields on
  the `create_order` and `log_customer_report` branches.
- [x] `app/config.py` — per-stage Settings fields:
  `copilot_router_model`, `copilot_main_model`, `copilot_report_model`,
  `copilot_title_model`.
- [x] `app/schemas/copilot.py` — `ChatTurnIn`, `ConversationCreateIn`,
  `DraftConfirmIn`, plus the read shapes (`ChatTurnOut`,
  `ConversationOut`, `ConversationListItem`, `ConversationListOut`,
  `ConfirmResultOut`).
- [x] `app/routes/copilot.py` — HTML routes (GET `/copilot`, GET
  `/copilot/{id}`), HTMX partials (POST `/copilot/turns`, POST
  `/copilot/conversations`, POST `/copilot/turns/{id}/confirm-order`,
  POST `/copilot/turns/{id}/confirm-report`, GET `/copilot/_rail`),
  JSON API mirrors under `/api/copilot/*`. Drafts are stashed inside
  the assistant turn's `tool_calls` JSONB as synthetic
  `pending_order_draft` / `pending_report_draft` records — no separate
  drafts table.
- [x] `app/main.py` + `app/templates/base.html` — router mounted; nav
  link added between Dashboard and Inbox.
- [x] `app/templates/copilot.html` + 7 partials (rail, thread,
  user/assistant bubbles, draft-order card, draft-report card,
  confirm-result, turn-pair) — three-pane shell, sticky composer,
  auto-grow textarea, Enter-to-send.
- [x] `app/static/waggle.css` — chat styling (3-column layout, message
  bubbles, tool-call chips, evidence expanders, draft cards, composer,
  in-flight pulse, mobile collapse).
- [x] `tests/schema.sql` — appended `conversations` + `chat_turns` DDL
  (mirrors alembic migration 003) so the per-test fresh-schema fixture
  drops + recreates them.
- [x] `tests/test_routes_copilot.py` — **19 E2E tests** covering shell
  rendering, all 4 intents, draft confirmation round-trips,
  conversation rail, JSON API mirrors. Real Postgres + real LLM.
- [x] `tests/test_e2e_copilot_browser.py` — **11 Playwright tests**
  driving Chromium against a uvicorn subprocess: shell, composer,
  conversation switch, confirm-order round-trip, error paths,
  double-submit guard.
- [x] **Layer 1 verification** (per `.claude/rules/ui-verification.md`):
  per-element subagent walk via Playwright MCP — 12/12 PASS (page
  load, Send button, tool-chip rendering, evidence expander, +new
  conversation, rail switch, create-order draft + confirm,
  log-report draft + confirm, Enter-key submit, send-button disabled
  during request, cross-page nav highlight, 404 on unknown conv).
- [x] **Layer 2 verification**: `pytest tests/test_e2e_copilot_browser.py`
  → 11/11 pass. Full project suite: **276 passed, 1 skipped**.

### Test counts after Phase F

| Suite | Pass | Skip | Notes |
|---|---|---|---|
| `make test` (full) | 276 | 1 | Real LLM + real pgvector + real fs |
| `pytest tests/test_e2e_copilot_browser.py` | 11 | 0 | Real Chromium |
| `pyright` on touched files | 0 errors, 2 warnings | — | Same baseline as `app/routes/pages.py` |

## Decisions open

- [ ] Final fictional ERP name (placeholder: "Hive-side ERP mock").
- [ ] Sandbox mode default — recommend OFF so ERP mock writes happen in the demo.
- [ ] Brand identity: custom honey-amber palette + hex-tile logo vs. stock.
- [ ] Whether to publish `agentic-mcp-crm/` as open source — pending decision.

## How to run (post-Phase-B)

```bash
make up           # boot Postgres (pgvector) + Redis via Docker
make migrate      # run local alembic migrations against the agentic_mcp_crm DB
make seed         # populate 40 customers, 150 orders, 40 inspections, ...
make demo         # start api + worker, open http://localhost:5001
make test         # full E2E suite against a fresh test DB
make test-unit    # Pydantic-only tests, no DB required
make reset-db     # drop everything and rerun migrate+seed
```

Every command is idempotent. Every failure is loud. No external services.
