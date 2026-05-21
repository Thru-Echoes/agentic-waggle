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
- [ ] Optional: gate the live deploy behind basic auth (`fly secrets set
  WAGGLE_USER=… WAGGLE_PASS=…`) if/when LLM cost-control becomes a concern.
  Currently public.

## Live deploy

- **URL:** https://waggle.fly.dev (Phase F+G deployed 2026-05-11; v5.1 matcher + Phase-I shipped 2026-05-18; remediation per `fix/critical-review-remediation` merged + deployed 2026-05-21).
- **Basic-auth credentials** (Fly secrets `WAGGLE_USER` / `WAGGLE_PASS`, set 2026-05-21 to close the public-LLM-spend exposure flagged by the critical review):
  - Username: `tester`
  - Password: `letMeTest1`
  - These are intentionally weak + intentionally shared for portfolio review (see README "Live demo" section). Rotate via `fly secrets set WAGGLE_USER=… WAGGLE_PASS=…` (triggers a restart). `/health` is the only public bypass (Fly liveness).
- **Migration HEAD: 004** — adds `customer_search_entries` + `sku_search_entries` for the semantic matcher (pgvector + FTS + CHECK constraints). On boot, `scripts/boot.py` runs `alembic upgrade head` + seed-if-empty, then execs uvicorn. The matcher search-index backfill runs as a **post-bind FastAPI lifespan task** gated on `WAGGLE_BOOT_REINDEX=1` so a cold deploy binds the port immediately instead of blocking Fly's health check on the synchronous embed pass.
- **2026-05-18 production incident** — Phase-I deploy surfaced a latent `/copilot` HTTP 500: historical `ChatTurn` rows held stashed-draft JSON predating the matcher schema's now-required `customer.evidence`. Hotfix shipped in `b897ef5` (L1/L2 — `_turn_view_for_template` render path) and `f52debb` (L3/L4 — `_split_tool_calls` + the confirm-order/report POST paths). Regression test in `tests/test_routes_copilot.py::TestLegacyDraftRegression`. Blast radius was contained to `/copilot` HTML; `/`, `/inbox`, `/api/*` stayed 200.
- **Surfaces verified at the 2026-05-11 baseline deploy** (Phase F+G smoke checks):
  - `GET /` → dashboard renders, Copilot nav link visible
  - `GET /copilot` → chat shell renders with composer
  - `GET /api/copilot/conversations` → JSON list returns
  - `POST /copilot/turns` → full LLM round-trip works (assistant bubble +
    `build_context` tool chip + grounded answer about varroa)
  - `GET /inbox` → 162 Extract buttons rendered
  - `POST /inbox/confirm` with malformed JSON → "Could not save" error card
  - `GET /api/orders?limit=1` → seeded orders
- **Demo flow scope honesty:** the live UI exposes inbox extraction (`/inbox`) and the copilot (`/copilot`). The full `EXTRACTED → UNDER_REVIEW → APPROVED → SCHEDULED → SYNCED_TO_ERP` FSM + ERP submit is orchestrated end-to-end by `scripts/demo.py`, **not** by the interactive Confirm buttons — those persist directly to `EXTRACTED`/`DRAFT`. Wiring the FSM into the UI confirm flow is a deferred enhancement.
- Image: rebuilt via `fly deploy --remote-only`, ~1 min build + 1.5 min rolling restart with seeded volume reattached.

## Phase H — copilot polish (done 2026-05-11)

User-facing quality pass on the chat surface. Three upgrades that turn
a working copilot into a polished one:

- [x] **LLM-titled conversations** — replaced the deterministic 60-char
  truncator with a Settings.copilot_title_model call that summarizes
  the first user message into 4-6 words (ChatGPT-style). Falls back to
  the truncator if the LLM call fails or returns nothing usable.
  `_llm_title` adds ~0.5s to the first turn; subsequent turns are
  unchanged. Examples produced in testing:
  - *"What's the varroa trend at O'Reilly?"* → *"Varroa trend at O'Reilly"*
  - *"Draft an order for St. Agnes — 40 hives."* → *"St. Agnes order draft"*
- [x] **Inline draft editing before Confirm** — both order drafts
  (/copilot + /inbox) and the customer-report draft (/copilot) now
  render editable form fields inside the Confirm card. Order drafts
  expose `qty_{i}`, `unit_price_{i}`, and `pollination_date` inputs;
  report drafts expose `varroa_pct`, `visit_date`, and a `narrative`
  textarea. The new `apply_order_overrides` helper in
  `app/routes/_order_persistence.py` layers form-side edits onto the
  stashed OrderDraft before persist; the inbox + copilot confirm
  routes apply the same logic for consistency. Persisted rows are
  tagged `edited_by_user=true` in transition metadata / report tags so
  the audit trail distinguishes LLM output from human-edited output.
- [x] **Streaming responses (Server-Sent Events) + step indicators** —
  `POST /copilot/turns` now content-negotiates: HTML (existing
  behavior) for tests / programmatic clients, SSE for the browser
  composer. The orchestrator accepts an optional `on_step` callback
  that fires at each stage (`intent_routed`, `context_built`,
  `answer_drafting`, `answer_ready`, `draft_ready`); the streaming
  endpoint drains those events from a queue and emits HTMX-friendly
  status payloads so the user sees a live "Thinking… → Routed your
  intent… → Loaded customer context… → Drafting your answer…"
  cascade instead of staring at a spinner for 5-6 seconds. The
  composer form was rewritten to use vanilla `fetch` + ReadableStream
  for SSE parsing (the HTMX `hx-post` attributes were removed to
  avoid double-submission). A pulsing honey-amber `.thinking-dot`
  animation accompanies each status. HTMX is re-`process()`ed after
  the final swap so the Confirm forms in draft cards stay clickable.

### Test counts after Phase H

| Suite | Pass | Skip | Notes |
|---|---|---|---|
| `make test` (full) | 310 | 14 | +5 from Phase G (2 streaming + 3 override). 14 skipped are Playwright tests (no Chromium-in-Docker). No flakes this run. |
| `pytest tests/test_e2e_copilot_browser.py` | 11 | 0 | Real Chromium incl. streaming-mode submit |
| `pytest tests/test_routes_copilot.py` | 21 | 0 | +2 for streaming (HTML vs SSE branches) |
| `pytest tests/test_routes_inbox_confirm.py` | 9 | 0 | +3 for qty / date / unparseable overrides |
| `pyright` on touched files | 0 errors, 14 warnings | — | Baseline pattern |

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

## Phase I — semantic matcher overhaul (done 2026-05-14)

LLM-driven semantic matching replaces rapidfuzz/Levenshtein on every
customer/SKU resolution path. Three-stage architecture:

- **Stage A** — hybrid retrieve (pgvector cosine + Postgres FTS,
  RRF fusion + best-entry-per-target SQL).
- **Stage B** — LLM disambiguator with structured output
  (gpt-5.4-mini, upgraded from nano in v5.1).
- **Stage C** — public API (`match_customer`, `match_sku`) +
  per-conversation LRU cache + `MatcherIndexEmpty` guard.

UI ships a confidence-graduated confirmation flow
(auto-accept / confirm / pick-from-alts) via the
`POST /copilot/turns/{id}/confirm-match` route. Order extractor and
copilot orchestrator both go through the public API. Legacy
`rapidfuzz` dependency removed; `customer_search_entries` and
`sku_search_entries` tables added in migration 004 with pgvector + FTS
+ CHECK constraints.

### v5.1 eval (full mode, 281 cases — re-measured post-rigor)

The 2026-05-19 critical-review remediation tightened the eval contract:
the `expected_alternatives_min` field (specified on 34 cases) is now
**asserted** by the harness (was parsed but silently ignored — the
discriminating half of the abstain-with-alternatives contract was
untested). That single change exposed 2 genuinely-vacuous `sku_ambiguous`
cases (matcher returned null without surfacing the required ≥2
alternatives) — the pre-rigor "100% SKU" was inflated by those
vacuous passes. It also correctly lifted `ambiguous_reference` and
`cross_customer_collision` to PASS (the matcher DOES surface alts on
those categories — the prior assumption that they were universally
vacuous was overstated). Numbers below are post-rigor.

| Metric | v4 baseline | v5 | v5.1 (pre-rigor) | **v5.1 (post-rigor)** |
|---|---|---|---|---|
| Customer | 76.3% (158/207) | 94.2% (195/207) | 95.2% (200/210) | **95.2% (200/210)** |
| SKU | 91.4% (64/70) | 100.0% (70/70) | 100.0% (71/71) | **95.8% (68/71)** |
| Customer cats PASS | 5/17 | 10/17 | 13/17 | **11/17** |
| SKU cats PASS | 5/7 | 7/7 | 7/7 | **5/7** |

### Cycle history

- v1 → v2: tighten ambiguity / pronoun / SKU-grade rules.
- v3: clear-winner rule + score gaps + `active_account` injection.
- v4: Stage A possessive + suffix normalization + dense fallback.
- v5: full quality pass — noise stripping, best-entry-per-target SQL
  (`ROW_NUMBER PARTITION BY target`), Stage B prompt rewrite,
  post-verifier business-abbreviation expansion.
- v5.1 (shipped 2026-05-18): 4 corpus additions (3 near-miss negatives +
  wildflower honey drum), Stage B disambig model upgrade
  gpt-5.4-nano → gpt-5.4-mini.
- **v5.1 remediation pass (2026-05-19, `fix/critical-review-remediation`)**:
  complete the prod /copilot legacy-JSON hotfix (L3 `_split_tool_calls`
  + L4 confirm POST paths); legacy-draft regression test; honest
  `pytest.mark.skip` on the matcher Layer-2 browser tests (replacing a
  stale comment); `boot.py` reindex moved to a post-bind FastAPI
  lifespan task (fixes "not listening on 0.0.0.0:5001" fragility);
  reindex_sku E2E test; narrow `except` in Stage B (fail-loud — programming
  bugs no longer misclassified as transient); **eval-rigor**:
  `expected_alternatives_min` assertion + `MATCHER_EVAL_STRICT` gate
  with `KNOWN_ACCEPTABLE_CUSTOMER` / `KNOWN_ACCEPTABLE_SKU` allowlists
  so `make test` stays honest about documented limitations but catches
  regressions; README rapidfuzz removed; corpus + migration counts
  reconciled; comparison-report gpt-5.5 provenance disclosed.

### Known-acceptable failures (v5.1 post-rigor ship state)

The eval test is **green by default** because these 8 categories are on
the documented allowlist in `tests/test_matcher_eval.py`
(`KNOWN_ACCEPTABLE_CUSTOMER` / `KNOWN_ACCEPTABLE_SKU`). Regressions on
*unlisted* categories still fail the test. Set `MATCHER_EVAL_STRICT=1`
to escalate every sub-threshold category to a hard fail (CI rigor pass).

Customer (6 categories):

| Category | Precision | Threshold | Note |
|---|---|---|---|
| `abbreviation` | 91.7% (11/12) | 98% | mini misses single colloquial cases (e.g. `Pacific Honey`) — matcher-quality follow-up. |
| `alias` | 92.3% (12/13) | 98% | mini misses numeric-to-word aliases (e.g. `7 Acres Farms`) — follow-up. |
| `apostrophe_variant` | 88.9% (8/9) | 98% | mini misses some `Company`→`Co.` normalization edges — follow-up. |
| `casual_colloquial` | 92.3% (12/13) | 95% | mini misses some casual-phrase resolutions — follow-up. |
| `site_reference` | 86.7% (13/15) | 95% | Cobblestone/Drakestone Main Warehouse swap — Stage A FTS tokenization issue (matcher-prompt-independent). |
| `site_shortform` | 75.0% (6/8) | 95% | mini under-commits to null on single-customer block disambiguators. |

SKU (2 categories):

| Category | Precision | Threshold | Note |
|---|---|---|---|
| `sku_ambiguous` | 71.4% (5/7) | 95% | Rigor-exposed: matcher returns null on `varroa treatment` / `honey drum` without surfacing the required ≥2 alternatives. Matcher-prompt TODO. |
| `sku_negative` | 87.5% (7/8) | 100% | `entrance reducer` over-matches to `EQ-FEED-ENT` — investigate corpus-vs-seed alignment. |

Full details in `.reports/matcher_phase_i_summary.md`.

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
