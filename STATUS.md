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

- [ ] Record a 60-90s screen capture of the demo for the portfolio site.
- [ ] Optional: hosted live demo (Fly.io or similar). Requires a budget cap
  on the LLM key and a magic-link / basic-auth gate.

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

## Phase G — completion sweep (done 2026-05-11)

Closes the remaining loose ends so the project is end-to-end whole: the
inbox surface now confirms-and-persists like the copilot does, the MCP
servers are smoke-tested + documented for client wiring, the demo
walks every surface, and the README reflects the three-surface
architecture.

- [x] **Shared order-persist helper** —
  `app/routes/_order_persistence.py` extracts `persist_order_from_draft`
  + `resolve_default_apiarist` from the copilot route. Both `/copilot`
  confirm and `/inbox` confirm now go through it.
- [x] **Inbox confirm-and-persist** — `partials/agent_draft.html` now
  ships with a Confirm form (round-trips the OrderDraft JSON through a
  hidden field). New route `POST /inbox/confirm` validates the draft
  back, persists via the shared helper, returns a confirm-result
  partial. The Confirm button is disabled when the customer didn't
  match (renders an inline "No customer match — cannot persist"
  message instead of letting a FK-violating insert through).
- [x] **MCP server smoke tests** — `tests/test_mcp_smoke.py` boots both
  servers via `python -m mcp_servers.X`, asserts the FastMCP banner
  appears, and confirms no import-time traceback.
- [x] **MCP search_customer_reports coverage** — added
  `TestSearchCustomerReports` to `tests/test_mcp_sales_copilot.py`
  (was the one untested tool on either server).
- [x] **.mcp.json.example** — copy-paste-ready MCP client config for
  both servers; supports Claude Desktop, Claude Code, Codex, Cursor.
- [x] **README sweep** — three-surface architecture description,
  `/copilot` added to the HTML pages table, new copilot routes added
  to the JSON API table, "Connect an MCP client" section with the
  per-host config paths, MCP tool table.
- [x] **`scripts/demo.py` extended to 7 steps** — adds `[6/7] Chat
  copilot — programmatic conversation turn` (creates a conversation,
  posts a turn through the orchestrator, persists user + assistant
  ChatTurns) and `[7/7] MCP tool call — find_customer via fastmcp
  Client`. End state exercises every surface.
- [x] **Provider env-var fallbacks** — `providers/{inbox,erp,storage,mailer}.py`
  + `seed/emails.py` now read `INBOX_FIXTURE_ROOT` / `ERP_LOG_ROOT` /
  `STORAGE_ROOT` / `SENT_MAIL_ROOT` from env, falling back to the
  Docker bind-mount paths. Enables running the demo + tests outside
  Docker without rewriting the providers.
- [x] **Playwright graceful skip** — both browser test fixtures now
  skip cleanly when Chromium isn't installed (typical in CI / Docker),
  with an actionable "run `playwright install chromium`" message
  instead of a hard error.

### Layer 1 verification — per-clickable subagent walk on the inbox confirm flow

12/12 PASS on /copilot in Phase F + 8/8 PASS on the new /inbox confirm
flow in Phase G. The /inbox walk verified: page load + Inbox nav
highlight, Extract → draft render, Confirm form + draft_json hidden
field, button enabled-when-matched / disabled-when-not, Confirm →
"Saved" card, persistence verified in `/api/orders`, a second
extract+confirm on a different email, and a malformed-JSON edge case
returning "Could not save". Two full end-to-end round trips persisted
real orders (Cascade Orchard Partners $77,700 / 3 items, Martinez &
Sons Orchards $8,400 / 1 item).

### Test counts after Phase G

| Suite | Pass | Skip | Notes |
|---|---|---|---|
| `make test` (full) | 304 | 14 | 305 tests; 1 LLM-flaky (`test_customer_evidence_grounded_in_source` — passes on re-run); 14 skipped are Playwright tests that need Chromium-in-Docker |
| `pytest tests/test_e2e_copilot_browser.py` | 11 | 0 | Real Chromium |
| `pytest tests/test_e2e_inbox_browser.py` | 2 | 0 | Real Chromium |
| `pytest tests/test_routes_inbox_confirm.py` | 6 | 0 | TestClient (no browser needed) |
| `pytest tests/test_mcp_smoke.py` | 2 | 0 | stdio subprocess smoke |
| `python -m scripts.demo` (end-to-end) | 7 / 7 steps | — | Inbox → agent → FSM → ERP → RAG follow-up → chat copilot → MCP tool, all pass |
| `pyright` on every touched file | 0 errors, 14 warnings | — | Same baseline pattern (UUID-vs-ColumnElement) as the rest of `app/routes/` |

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
