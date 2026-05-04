# Waggle

> *The agentic CRM for the people who move the bees.*

**Waggle** is a self-contained, agentic CRM for a fictional commercial beekeeper ("Meridian Apiaries & Pollination Services"). It runs entirely on your laptop — Postgres + pgvector + Redis — with synthetic data and zero external services. No cloud dependency, no third-party SaaS, no mocks.

The name comes from the **waggle dance**: how bees tell each other where the nectar is. In Waggle, it's how agents tell each other where the data is.

The domain is migratory commercial beekeeping: 40 customers (orchards, honey wholesalers, mead makers, specialty food), 150 orders spanning pollination contracts and SKU shipments, 40 apiary inspection reports, and 80 synthetic inbound emails. The app showcases an **agentic order-extraction pipeline**, **two MCP servers** (Sales Copilot + Trip Reports), and a **local RAG stack** (pgvector + sentence-transformers) — all wrapped in a FastAPI + Jinja/HTMX frontend.

**Repo name:** `agentic-mcp-crm` (tech-descriptive, portfolio-safe, reusable if the domain shifts).
**Product name:** Waggle.

## Brand

- **Palette:** honey amber `#E89F2A`, dark comb `#2B1D0F`, pollen cream `#F6ECD3`, sap green `#4E6B3A` (accent for live/healthy), alarm carmine `#B83A3A` (accent for red-flags/regulatory).
- **Motif:** regular hexagon with an arrow through it at ~30°, echoing the waggle-dance orientation. Hex tiles for grids and list rows.
- **Typography:** a humanist sans for UI (Inter/IBM Plex Sans), a slab or serif for hero type (IBM Plex Serif) to keep it feeling a bit analog.

## What this is (and is not)

- **Is:** A fully standalone portfolio-grade CRM with a synthetic beekeeping dataset. Postgres, pgvector, Redis, FastAPI, LangGraph, MCP servers, real LLM (Anthropic / OpenAI). Zero external services required — everything runs on your laptop.
- **Is not:** A copy or fork of any other CRM. All code, data, and naming is original to this project.

## Quick start

```bash
cp .env.example .env             # then add OPENAI_API_KEY (or ANTHROPIC_API_KEY)
make build                        # build the api/seed image
make up                           # boot Postgres (with pgvector) + Redis via Docker
make migrate                      # run alembic migrations against the local DB
make seed                         # populate 40 customers, 150 orders, 40 inspections, embeddings
make demo                         # start the api at http://localhost:5001
```

Open `http://localhost:5001` and click around: dashboard → inbox → "Extract"
on any pollination request to drive the live LangGraph agent.

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

## Layout

```
agentic-mcp-crm/
├── Makefile                      command entry points
├── Dockerfile                    single image for api / worker / seed
├── docker-compose.yml            Postgres (pgvector) + Redis + api + worker
├── .env.example                  sample env vars
├── requirements.txt              Python deps for app + seed
├── pyrightconfig.json            strict type checking
├── STATUS.md                     phase-by-phase status + roadmap
├── alembic/                      migration chain (first migration creates schema)
│   ├── env.py
│   └── versions/
├── db/                           SQLModel models, session factory
│   ├── models.py                 17-table model layer
│   └── session.py                engine + session scope
├── app/                          FastAPI application
│   ├── main.py                   app factory
│   ├── routes/                   health, customers, orders, customer_reports,
│   │                             search, agent, pages (HTML)
│   ├── schemas/                  request/response Pydantic models
│   ├── static/                   waggle.css, logo.svg
│   └── templates/                Jinja pages + HTMX partials
├── agents/                       LangGraph pipelines
│   └── order_extractor.py        email → extracted order draft
├── mcp_servers/                  MCP servers (the portfolio's flagship)
│   ├── sales_copilot_server.py   customer / site / customer-report lookup + email drafting
│   └── customer_reports_server.py    narrative → structured fields, fleet-health queries
├── providers/                    first-class provider impls (real, not stubs)
│   ├── vector_search.py          sentence-transformers + pgvector cosine search
│   ├── storage.py                local filesystem object store
│   ├── erp.py                    JSON-line "Hive-side ERP" mock
│   ├── inbox.py                  fixture-backed Gmail replacement
│   ├── mailer.py                 outbound-mail log
│   └── llm.py                    real Anthropic / OpenAI only — no canned fallback
├── seed/                         synthetic data generator
│   ├── generate.py               entry point: python -m seed.generate
│   ├── models.py                 Pydantic domain models (intermediate)
│   ├── faker_providers.py        custom beekeeping Faker providers
│   └── (16 generator modules)
├── tests/                        21-module E2E suite
│   ├── conftest.py               DB + filesystem fixtures
│   ├── schema.sql                authoritative column-level schema
│   ├── test_seed_*.py            every generator exercised
│   ├── test_provider_*.py        every provider exercised
│   └── test_integration_full_seed.py
├── scripts/
│   └── demo.py                   5-minute demo orchestrator
├── local-storage/                stands in for object storage (gitignored contents)
├── sent-mail/                    log-only outbound (gitignored contents)
├── erp-sync-log/                 JSON log of ERP mock calls (gitignored)
└── gmail-fixtures/               fixture inbox the inbox provider reads from
```

## Pages and routes

**HTML pages** (browse `http://localhost:5001`):

| URL | What it is |
|---|---|
| `/` | Dashboard — customer / order / customer-report counts, FSM state breakdown, recent activity |
| `/inbox` | Synthetic inbound mail; click *Extract* to run the LangGraph agent live and see the resulting order draft inline |
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
| `GET  /health`, `GET /ready` | Liveness / readiness probes (root, not under `/api`) |

OpenAPI docs are at `/docs`; raw schema at `/openapi.json`.

## Flagship demo flow

1. **Inbound email arrives** — the inbox provider surfaces a fixture email ("Hi, can we get 20 Italian queens for our Central Valley site next week?").
2. **LangGraph agent extracts** — customer is fuzzy-matched, line items are resolved to SKUs, the order is drafted in state `EXTRACTED`. Visible from `/inbox`.
3. **Order goes through FSM** — `EXTRACTED → UNDER_REVIEW → APPROVED → SCHEDULED`.
4. **ERP sync fires** — the mock writes a JSON line to `/erp-sync-log/`; the order transitions to `SYNCED_TO_ERP`. Visible from `/erp-log`.
5. **Sales Copilot answers a question** — "What's the varroa trend at the O'Reilly orchard?" The MCP server queries customer reports, aggregates mite counts, and returns a structured answer with citations.

Each step is live, demo-able, and backed by real code — no canned responses, no mocks.

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

```bash
# Sales Copilot — customer/site lookup, customer-report search, email drafting
docker compose run --rm seed python -m mcp_servers.sales_copilot_server

# Customer reports — narrative parsing, fleet-health queries
docker compose run --rm seed python -m mcp_servers.customer_reports_server
```

Wire either of them into Claude Desktop, MCP Inspector, or another MCP
client by configuring it to launch the command above. The servers will
hold a session open on stdio and respond to tool calls. They share the
same `DATABASE_URL` and providers as the API.

## Guardrails

- **All synthetic.** Nothing in `seed/` represents real data — every customer name, SKU, and price is fictional and generated by the seed module.
- **Everything offline-capable.** The only network dependency is the LLM API. Set `OPENAI_API_KEY` in `.env` (primary, `gpt-5.4-mini`). To switch to Claude, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`. The app fails loudly at startup if the selected provider's key is missing and no fallback key is set either.
- **Fail-loud throughout.** No `ON CONFLICT DO NOTHING`, no silent FK skips, no canned fallbacks. See `.claude/rules/fail-loud.md` for the full list.
- **Gitignored runtime directories.** `local-storage/`, `sent-mail/`, `erp-sync-log/` hold generated artifacts; their contents are ignored. Keep `.gitkeep` files.

## Status

End-to-end boot is verified locally: postgres + pgvector + redis container
up, alembic migration creates 17 tables, seed populates 40 customers, 150
orders, 80 inbound emails, 40 customer reports with embeddings, FastAPI serves
`localhost:5001`, all HTML pages render, all JSON APIs return 200, the
LangGraph order-extraction agent runs against the real LLM and produces a
fuzzy-matched `OrderDraft`. Test suite: **187 passed, 0 failed, 8 skipped**.

See `STATUS.md` for the authoritative phase-by-phase status.
