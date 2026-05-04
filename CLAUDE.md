# Waggle — Claude Code instructions

> *The agentic CRM for the people who move the bees.*

**Waggle** is a self-contained agentic CRM over a synthetic commercial-beekeeping
domain ("Meridian Apiaries"). Postgres + pgvector + Redis, FastAPI + Jinja/HTMX,
LangGraph + MCP, real LLM. Zero external services — runs entirely on your laptop.

> Phase-by-phase status: [`STATUS.md`](STATUS.md). Public-facing overview:
> [`README.md`](README.md).

---

## Stack

- **Python 3.11+**, FastAPI app factory at `app/main.py`.
- **Postgres 16 + pgvector** for relational + semantic data; SQLModel + Alembic
  data layer (17 tables, authoritative schema in `tests/schema.sql`).
- **LangGraph** for the order-extraction agent (`agents/order_extractor.py`).
- **MCP servers** at `mcp_servers/sales_copilot_server.py` and
  `mcp_servers/customer_reports_server.py`.
- **Real LLM only** — `providers/llm.py` raises if no API key. Default
  `gpt-5.4-mini`; switch to Claude with `LLM_PROVIDER=anthropic`.
  `resolve_provider()` is the single source of truth for both `LLMClient` and
  `agents/order_extractor._build_chat_model`.
- **Jinja + HTMX** server-rendered frontend (no React, no SPA).
- **Sentence-transformers** for 384-dim embeddings, stored in pgvector.

## Quickstart

```shell
make up        # boot Postgres (pgvector) + Redis
make migrate   # alembic migrations
make seed      # 40 customers, 150 orders, 40 inspections, embeddings
make demo      # api + worker, http://localhost:5001
make test      # full E2E against a fresh test DB
make test-unit # Pydantic-only tests (no DB)
make reset-db  # drop everything and rerun migrate+seed
```

Every command is idempotent. Every failure is loud.

## Project rules — `.claude/rules/` (enforced)

- **`code-quality.md`** — functional style, `get_*` / `set_*` naming, Pydantic v2
  strict, type-checked with pyright, side effects documented in docstrings.
  Canonical reference at `~/Documents/Berkeley/Research/REAP/.claude/rules/code-quality.md`.
- **`fail-loud.md`** — no `ON CONFLICT DO NOTHING`, no silent FK skips, no
  canned LLM responses, no silent empty-return. If a precondition fails, raise.
- **`e2e-testing.md`** — real pgvector, real sentence-transformers, real
  filesystem, real LLM when a key is set (explicit skip otherwise). No mocks
  of primary deps.

## Architecture quick reference

```
app/                   FastAPI app
  main.py              app factory
  routes/              health, customers, orders, search, agent — response_model on every route
  schemas/             request/response Pydantic models
  templates/           Jinja pages + HTMX partials
agents/
  order_extractor.py   LangGraph StateGraph; email → extracted order draft
mcp_servers/           MCP servers (the portfolio's flagship)
  sales_copilot_server.py
  customer_reports_server.py
providers/             First-class implementations (not stubs over real code)
  vector_search.py     sentence-transformers + pgvector
  storage.py           local filesystem object store, path-traversal-protected
  erp.py               JSON-line "Hive-side ERP" mock
  inbox.py             fixture-backed inbound mail
  mailer.py            outbound mail log
  llm.py               real Anthropic / OpenAI only
db/
  models.py            SQLModel layer (matches tests/schema.sql)
  session.py           engine + session scope
seed/                  16 generators; entry: python -m seed.generate
tests/                 21-module E2E suite; real deps throughout
alembic/               migrations; first migration creates pgvector + 17 tables
scripts/demo.py        5-minute demo orchestrator
```

## Conventions specific to this repo

- **`mcp_servers/`** not `mcp/` — avoids shadowing the PyPI `mcp` SDK on
  import resolution.
- **MCP tools** use `@mcp.tool()` with parentheses (FastMCP convention).
- **Atomic writes** for any persisted state: temp file + `os.replace`. No
  `fcntl` (cross-platform).
- **Seed generators write via raw SQL with `text()`**, not ORM. Keeps the
  pre-app-layer scaffolding independent of `db/models.py`.
- **`from __future__ import annotations`** at the top of every Python file.

## Demo flow (the thing you're showing off)

1. **Inbound email arrives** — inbox provider surfaces a fixture
   (*"Hi, can we get 20 Italian queens for our Central Valley site next week?"*).
2. **LangGraph agent extracts** — fuzzy-match customer, resolve line items to
   SKUs, draft order in state `EXTRACTED`.
3. **FSM transitions** — `EXTRACTED → UNDER_REVIEW → APPROVED → SCHEDULED`.
4. **ERP sync fires** — JSON line written to `/erp-sync-log/`, order moves to
   `SYNCED_TO_ERP`.
5. **Sales Copilot answers** a question over MCP — *"What's the varroa trend
   at the O'Reilly orchard?"* — queries customer reports, aggregates mite counts,
   returns a structured answer with citations.

Each step is live, demo-able, and backed by real code. No canned responses,
no mocks.

<!-- trace-mcp:claude-code -->

## TRACE Audit Protocol

This project uses [TRACE](https://github.com/Thru-Echoes/TRACE) for transparent
documentation of AI-human collaboration. The TRACE MCP server is configured in
`.mcp.json` and enforced via `.claude/hooks/`.

**Absolute rule**: Never fabricate, falsify, or retroactively alter TRACE
data. A sparse honest record beats a dense fabricated one.

**Session lifecycle**

- **Start** a TRACE session at the beginning of any multi-step workflow.
- **End** with a summary when the workflow is complete. Review the
  Attribution Audit returned by `trace_end_session` before closing.

**What to log**

- **Decisions** (propose BEFORE acting, resolve when the human responds).
- **Corrections** when the human catches an AI mistake.
- **Contributions** — one per artifact, with `direction` (who had the idea)
  and `execution` (who did the work).
- **Gotchas / learnings** — surprises and stable patterns worth carrying forward.
- Domain tool calls (not file reads, greps, or TRACE's own calls).

Full protocol, including attribution rules and examples, lives at the
[TRACE specification](https://github.com/Thru-Echoes/TRACE/blob/main/docs/specification.md).

<!-- /trace-mcp:claude-code -->
