# Waggle

> *The agentic CRM for the people who move the bees.*

**Waggle** is an agentic CRM for a synthetic commercial-beekeeping domain — Meridian Apiaries & Pollination Services.

It surfaces the same data three ways, so every layer of the stack is visible end-to-end:

1. **An inbox + agent flow** — open `/inbox`, click *Extract* on an inbound email, watch the LangGraph order-extraction pipeline draft a structured order with evidence quotes, then *Confirm* to persist it.
2. **A chat copilot at `/copilot`** — a Jinja+HTMX chat UI that routes each turn to one of four tools (RAG-grounded query, order draft, customer-report draft, command), persists every conversation, and lets the salesperson confirm drafts inline.
3. **Two MCP servers** — `mcp_servers/sales_copilot_server.py` and `mcp_servers/customer_reports_server.py` expose the same data over the Model Context Protocol so any MCP client (Claude Desktop, Codex, Cursor, …) can do customer lookup, vector search, fleet-health aggregation, and email drafting.

All three share the same first-class providers (`vector_search`, `inbox`, `erp`, `mailer`, `storage`, `llm`), the same Postgres + pgvector store, and the same fail-loud policy (`.claude/rules/fail-loud.md`).

The name comes from the **waggle dance** — how bees tell each other where the nectar is. In Waggle, it's how agents tell each other where the data is.

Clone the repo, set an `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) in `.env`, and run `make build && make up && make migrate && make seed && make demo`. Postgres + pgvector + Redis come up under Docker; no other external dependencies.

## Data 

- 40 customers (orchards, honey wholesalers, mead makers, specialty food)
- 150 orders across 11-state FSM 
- 40 apiary inspections 
- 80 inbound emails 


## Brand

- **Palette:** honey amber `#E89F2A`, dark comb `#2B1D0F`, pollen cream `#F6ECD3`, sap green `#4E6B3A` (accent for live/healthy), alarm carmine `#B83A3A` (accent for red-flags/regulatory).
- **Motif:** regular hexagon with an arrow through it at ~30°, echoing the waggle-dance orientation. Hex tiles for grids and list rows.
- **Typography:** a humanist sans for UI (Inter/IBM Plex Sans), a slab or serif for hero type (IBM Plex Serif) to keep it feeling a bit analog.

## Quick start

```bash
cp .env.example .env             # then add OPENAI_API_KEY (or ANTHROPIC_API_KEY)
make build                        # build the api/seed image
make up                           # boot Postgres (with pgvector) + Redis via Docker
make migrate                      # run alembic migrations against the local DB
make seed                         # populate 40 customers, 150 orders, 40 inspections, embeddings
make demo                         # start the api at http://localhost:5001
```

Open `http://localhost:5001` and click around:

- **Dashboard** — counts, FSM state breakdown, recent activity.
- **Copilot** — chat with Waggle. Ask a question, draft an order, log a visit report. Each turn is persisted; drafts are confirm-and-save.
- **Inbox** — synthetic inbound mail. Click *Extract* on a row to run the LangGraph agent, then *Confirm* to persist the drafted order.
- **Customers / Orders / Customer reports / ERP log** — the underlying data.

Full reset:
```bash
make reseed     # drop data, regenerate everything
make reset-db   # drop DB entirely and rerun migrate+seed
```

Tear down:
```bash
make down
```

Run tests:
```bash
make test       # full E2E suite (real pgvector, real sentence-transformers, real LLM)
make test-unit  # Pydantic-only tests (no DB needed)
```

## Architecture

A browser hits FastAPI; an MCP client speaks to the same backend over stdio.
The AI/data-science layer sits between them and the database.

```mermaid
flowchart TB
    U1[Browser]
    U2["MCP client<br/>(Claude Desktop, Inspector, …)"]

    subgraph Web["FastAPI + Jinja/HTMX"]
      direction LR
      Pages[HTML pages]
      API["JSON API · /api/*"]
    end

    subgraph AI["AI / data-science layer"]
      direction TB
      Agent["LangGraph order-extraction agent<br/>classify → fuzzy-match customer →<br/>structured-output line items →<br/>SKU resolution (rapidfuzz + LLM fallback)"]
      RAG["Local RAG<br/>sentence-transformers (384-dim) +<br/>pgvector cosine semantic search"]
      MCP["MCP servers<br/>Sales Copilot · Customer Reports<br/>(tools over stdio)"]
    end

    LLM[("OpenAI / Anthropic<br/>(gpt-5.4-mini · claude-sonnet-4-6)")]
    DB[("Postgres + pgvector<br/>17 tables · seeded synthetic data")]

    U1 --> Web
    U2 -->|stdio| MCP
    Web --> Agent
    Web --> RAG
    Agent --> LLM
    Agent --> DB
    RAG --> DB
    MCP --> DB
    MCP --> RAG
    MCP --> LLM
```

Three pieces worth highlighting from a data-science / AI-engineering lens:

- **Structured-output extraction.** The LangGraph agent uses
  `with_structured_output(PydanticModel)` so each node returns validated
  Pydantic objects, not free-text JSON. Fuzzy customer matching uses
  `rapidfuzz`; SKU resolution falls back to an LLM guess only when the
  fuzzy score is below threshold.
- **Local semantic search.** Customer-report narratives are chunked, embedded
  with `sentence-transformers/all-MiniLM-L6-v2` (384-dim), and stored in
  `pgvector`. The Sales Copilot MCP tool runs cosine similarity directly
  in Postgres — no external vector DB.
- **MCP-native surface.** The same data the API exposes via JSON is also
  reachable through MCP tools (customer lookup, customer-report search,
  email drafting), so an LLM client can answer business questions without
  scraping the UI.

## Layout

```
agentic-mcp-crm/
├── app/                FastAPI app — routes, Jinja templates, schemas, static
├── agents/             LangGraph order-extraction pipeline
├── mcp_servers/        Sales Copilot + Customer Reports MCP servers
├── providers/          First-class implementations
│                       (vector_search, inbox, erp, mailer, storage, llm)
├── db/                 SQLModel layer (17 tables) + session
├── alembic/            DB migrations (single migration creates schema)
├── seed/               16 generators that populate synthetic data
├── tests/              21-module E2E suite (real pgvector, real LLM)
├── scripts/demo.py     CLI orchestrator: email → FSM → ERP → Copilot
├── Dockerfile          single image used by api, seed, and MCP servers
├── docker-compose.yml  postgres + redis + api
└── Makefile            command entry points (`make help`)
```

## Pages and routes

**HTML pages** (browse `http://localhost:5001`):

| URL | What it is |
|---|---|
| `/` | Dashboard — customer / order / customer-report counts, FSM state breakdown, recent activity |
| `/copilot` | Three-pane chat UI (rail + thread + composer) for the sales copilot. Each turn is persisted; drafts (orders / visit reports) render inline with a *Confirm & save* button |
| `/inbox` | Synthetic inbound mail; click *Extract* to run the LangGraph agent live and see the resulting order draft inline, then *Confirm* to persist it to `orders` |
| `/customers` | Customer directory, market segments, ERP numbers |
| `/orders` | Filterable by FSM state |
| `/customer-reports` | Apiary inspections; click a row to see the full narrative + varroa / disease detail |
| `/erp-log` | Browse the local ERP-sync log (JSON-line events) |

**JSON API** (under `/api`):

| Method + URL | Purpose |
|---|---|
| `GET  /api/customers`, `GET /api/customers/{id}` | List + detail |
| `GET  /api/orders`, `GET /api/orders/{id}` | List (filterable by `state`, `customer_id`) + detail |
| `GET  /api/customer-reports`, `GET /api/customer-reports/{id}` | List + detail |
| `POST /api/agent/extract` | Run LangGraph extraction on an `EmailInput`; returns `OrderDraft` or `ExtractionFailure` |
| `POST /api/search` | Cosine search against `chunks` via pgvector + sentence-transformers |
| `GET  /api/copilot/conversations`, `GET /api/copilot/conversations/{id}` | Sales-copilot conversations (read) |
| `POST /api/copilot/conversations` | Create a conversation programmatically |
| `POST /api/copilot/turns/{turn_id}/confirm-order`, `…/confirm-report` | Persist a copilot-drafted order or visit report |
| `GET  /health`, `GET /ready` | Liveness / readiness probes (root, not under `/api`) |

OpenAPI docs are at `/docs`; raw schema at `/openapi.json`.

## Flagship demo flow

1. **Inbound email arrives** — the inbox provider surfaces a fixture email ("Hi, can we get 20 Italian queens for our Central Valley site next week?").
2. **LangGraph agent extracts** — customer is fuzzy-matched, line items are resolved to SKUs, the order is drafted in state `EXTRACTED`. Visible from `/inbox`.
3. **Order goes through FSM** — `EXTRACTED → UNDER_REVIEW → APPROVED → SCHEDULED`.
4. **ERP sync fires** — the mock writes a JSON line to `/erp-sync-log/`; the order transitions to `SYNCED_TO_ERP`. Visible from `/erp-log`.
5. **Sales Copilot answers a question** — "What's the varroa trend at the O'Reilly orchard?" The MCP server queries customer reports, aggregates mite counts, and returns a structured answer with citations.

The full FSM walk (steps 2-5) is also scripted in `scripts/demo.py`:

```bash
docker compose run --rm seed python -m scripts.demo
# or with a specific account / message:
docker compose run --rm seed python -m scripts.demo --account-id sam.oreilly@meridian-apiaries.test
```

This drives one inbound email all the way from extraction through ERP sync,
including a Sales-Copilot-style follow-up email draft. Output is labeled
step-by-step (`[1/5] Preflight` … `[5/5] Copilot`).

## MCP servers

Two servers live under `mcp_servers/`. They are stand-alone Python entry
points, not part of the FastAPI process — they speak the
[Model Context Protocol](https://modelcontextprotocol.io/) over stdio.

| Server | Tools |
|---|---|
| `mcp_servers.sales_copilot_server` | `find_customer`, `list_customer_sites`, `list_recent_orders`, `search_customer_reports`, `draft_followup_email` |
| `mcp_servers.customer_reports_server` | `extract_from_narrative`, `query_by_disease`, `query_by_varroa_threshold`, `aggregate_fleet_health` |

### Run a server directly

```bash
# Sales Copilot — customer/site lookup, customer-report search, email drafting
docker compose run --rm seed python -m mcp_servers.sales_copilot_server

# Customer reports — narrative parsing, fleet-health queries
docker compose run --rm seed python -m mcp_servers.customer_reports_server
```

### Connect an MCP client

A ready-to-edit config lives in [`.mcp.json.example`](.mcp.json.example).
Copy it into your MCP client's config location:

| Client | Config path |
|---|---|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) |
| Claude Code (CLI) | project `.mcp.json` |
| Codex | `~/.codex/mcp.json` |
| Cursor | project `.cursor/mcp.json` |

Then fill in:

- `cwd` — absolute path to your local clone of this repo.
- `DATABASE_URL` — point at the host-mapped Postgres
  (`postgresql://agentic_mcp_crm:agentic_mcp_crm@localhost:5433/agentic_mcp_crm`
  when running via `make up`).
- `OPENAI_API_KEY` — for tools that call the LLM
  (`draft_followup_email`, `extract_from_narrative`).

The servers hold a session open on stdio and respond to tool calls. They
share the same `DATABASE_URL` and providers as the FastAPI app, so an
order persisted via `/copilot` or `/inbox` is immediately visible to an
MCP `list_recent_orders` call.

## Guardrails

- **All synthetic data.** Customer names, SKUs, prices, and emails are fictional, generated by `seed/`.
- **LLM provider toggle.** Default is OpenAI (`gpt-5.4-mini`). Switch with `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`. The app raises at startup if the selected provider's key is missing.

## Status

See `STATUS.md` for the authoritative phase-by-phase status.
