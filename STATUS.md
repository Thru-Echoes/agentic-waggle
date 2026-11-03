# Waggle — Status

What works, how it is verified, what is known to be missing. Kept honest on
purpose: a reviewer who finds a gap here before finding it in the demo is the
point.

## At a glance (2026-11-02)

| Surface | State | Verified by |
|---|---|---|
| Inbox → LangGraph extraction → Confirm | works; drafts carry verbatim evidence; failures (including a rejected LLM key) render as cards | `tests/test_agent_order_extractor.py`, `tests/test_routes_inbox_confirm.py`, `tests/test_e2e_inbox_browser.py`, `tests/test_e2e_error_surfaces_browser.py` |
| Copilot (query / order draft / report draft) | works; conversations are scoped per visitor; low-confidence matches ask, then resume with the chosen customer; answers cite real report rows | `tests/test_routes_copilot.py`, `tests/test_routes_confirm_match.py`, `tests/test_agent_copilot_*.py`, `tests/test_e2e_copilot_*_browser.py` |
| Order FSM | enforced in `db/fsm.py`; visible on `/orders` (history, next steps, stale-state guard) | `tests/test_fsm.py`, `tests/test_routes_orders_fsm.py`, `tests/test_e2e_orders_browser.py` |
| Semantic matcher (Stage A hybrid retrieve + Stage B LLM disambiguation) | 2026-11-03 full-corpus eval with `gpt-6-luna`: customer 95.7–96.2% (201–202/210, three runs), SKU 98.6% (70/71); sample-mode eval runs in `make test` with a key | `tests/test_provider_semantic_matcher.py`, `tests/test_matcher_eval.py`, `.reports/` |
| MCP servers (2 servers, 9 tools) | work over stdio; smoke-tested and tool-tested | `tests/test_mcp_*.py` |
| HTTP surface | Basic auth with an HTML 401, nonce CSP + security headers, CSRF origin check, per-client rate limit + daily LLM budget, generic 500s with request ids, `/ready` reports the provider probe | `tests/test_security_surfaces.py`, `tests/test_ratelimit.py`, `tests/test_provider_probe.py` |
| Build | locked dependencies (`pyproject.toml` + `uv.lock`), multi-stage non-root image (2.75 GB), CI builds and boots the image | `.github/workflows/ci.yml` |

Test suite: 55 modules. Without an LLM key every non-model test runs
(real pgvector, real sentence-transformers, real Chromium); model-backed
tests skip with the documented reason. With a key the whole suite runs; the
matcher eval runs in sample mode and reports to `.reports/`.

## Known limitations (deliberate, documented)

- **One shared login.** `tester` / `letMeTest1` is published on purpose. Conversations are scoped per browser by a signed cookie; there is no per-user login, and confirmed rows are attributed to the demo's default apiarist.
- **No delete routes.** Confirmed orders, reports and conversations stay until removed by hand.
- **Seeded orders have no transition history** (the seed inserts rows directly); orders created through Confirm or the demo script do. The pane says which case it is.
- **Costs are list-price estimates** (`providers/llm.py::MODEL_PRICES_USD_PER_M`), for observability, not billing.
- **The rate limiter and daily budget are per process**, exact for the single Fly machine; Redis (already a dependency) is the path to more than one.
- **Matcher eval allowlist.** Eight categories sit below their thresholds with documented reasons (see `tests/test_matcher_eval.py::KNOWN_ACCEPTABLE_*`); unlisted regressions still fail, and `MATCHER_EVAL_STRICT=1` escalates everything. In sample mode, categories with fewer than five sampled cases are reported, not gated.
- **One model-backed test is nondeterministic**: `tests/test_provider_semantic_matcher.py::test_llm_disambiguate_picks_active_account_on_ambiguous_tie` depends on the model picking the active account on a deliberate tie and occasionally does not.
- **Anthropic path.** `LLM_PROVIDER=anthropic` is wired and unit-tested but the eval and benchmark numbers are OpenAI-only.

## Models

Chosen on the repository's own benchmark (`scripts/benchmark_models.py`,
`docs/benchmarks/2026-11-02-extract-models.md`): every candidate reached
25/25 accuracy; `gpt-6.1-terra` was the smallest model to ground 100% of
its evidence quotes verbatim on every stage, so it is the default for the
extraction stages and the copilot router/main/report stages; `gpt-6-luna`
titles conversations and runs the matcher's Stage B disambiguator, chosen on
the full 281-case corpus eval (`docs/benchmarks/2026-11-03-matcher-stage-b.md`):
`gpt-6-luna` (201–202/210 customers over three runs, 70/71 SKUs) and
`gpt-6.1-terra` (201/210, 70/71) tie,
within one customer case of `gpt-5.4-mini`, and luna costs a tenth of terra.
Every stage is a `Settings` field and an environment variable. Newer OpenAI
families reject an explicit temperature; `providers/llm.py` sends one only
to families known to accept it.

## Live deploy

- **URL:** https://waggle.fly.dev (Fly app `waggle`, region `iad`; Postgres is the sibling app `waggle-pg`, reachable only over Fly's private network, `pg/fly.toml`).
- **Credentials:** `tester` / `letMeTest1`, intentionally public (README "Live demo"). `/health` and `/ready` are the only unauthenticated routes besides static assets. `/health` carries the deployed commit (`git_sha`) and build time; `/ready` reports database, pgvector and the LLM-provider probe (`status: degraded` when the key is rejected).
- **Boot:** `scripts/entrypoint.sh` prepares the `/data` volume as root, drops to the `waggle` user, then `scripts/boot.py` runs `alembic upgrade head`, seeds if `customers` is empty, and execs uvicorn; the matcher index backfill and the provider probe run as post-bind tasks.
- **Secrets:** `OPENAI_API_KEY`, `DATABASE_URL`, `WAGGLE_USER`, `WAGGLE_PASS`; set `WAGGLE_SECRET_KEY` so visitor cookies survive restarts and `WAGGLE_DEMO_CREDENTIALS_PUBLIC=1` so the HTML 401 page prints the demo credentials. Rotate the model key with `fly secrets set OPENAI_API_KEY=…` (restarts the app).
- **Deploy:** `fly deploy --build-arg GIT_SHA=$(git rev-parse --short HEAD) --build-arg BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)`; the image is built from `uv.lock`.
- **Cost:** about $5/month at list prices while idle, about $18/month if the web machine never suspends (web machine shared-cpu-2x 2 GB, Postgres shared-cpu-1x 512 MB, 4 GB of volumes). The Fly proxy suspends the web machine when no requests arrive (`auto_stop_machines = 'suspend'`, `min_machines_running = 0`) and resumes it on the next request in under a second; a suspended machine bills only its root filesystem. A deploy or host maintenance discards the suspend snapshot, so the first request afterwards waits for a full cold start (migrations plus the embedding-model load, measured at about 33 s).

## History

- **Phase B–E (April 2026):** seed generators (16), providers (6), FastAPI app, LangGraph extractor, two MCP servers, demo script, 7 architecture diagrams (rendered copies live in the portfolio site, not in this repo).
- **Phase F–H (May 2026):** copilot backend + frontend, inbox Confirm, LLM-titled conversations, inline draft editing, SSE streaming, MCP client config.
- **Phase I (May 2026):** semantic matcher (hybrid retrieve + LLM disambiguation) replaced rapidfuzz; corpus eval harness with per-category thresholds; migration 004.
- **2026-05-18 incident:** historical stashed drafts predating the matcher schema made `/copilot` 500; tolerant parsing shipped. Those drafts now render as "Draft expired".
- **2026-11-02 audit remediation (seven pull requests):** locked dependencies, explicit psycopg2 driver (a fresh install had resolved SQLAlchemy 2.1, whose default driver is psycopg v3), CPU-only torch (image 11.1 GB → 2.75 GB), non-root image, CI, LICENSE; current-generation model defaults chosen by benchmark; visible provider failures, generic 500s, security headers, CSRF check, HTML 401; rate limit + daily budget + measured token/cost accounting; enforced and visible order FSM; visitor-scoped conversations, real confirm-match resume, persisted citations; this status rewrite, deterministic seed timestamps, `.test` domains, `docs/INVARIANTS.md`.

## Decisions open

- Keep Basic auth with the HTML 401 page, or an auth-exempt landing page with an "Enter demo" button.
- Whether to purge the pre-scoping conversations and the one visitor-confirmed order from the live database by hand.
- The matcher corpus case for a bare "Cascade" (expected ambiguous, resolved to Cascade Orchard Partners by every evaluated model): fix the corpus or the matcher.
- A 60–90 s screen capture of the working flow for the portfolio page.
- Visual polish pass on the UI (sequenced after the above lands).
