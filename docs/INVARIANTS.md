# Invariants

Rules the codebase enforces in one place and guards with a test, so a new
site that violates one fails the suite instead of waiting for a reviewer to
notice. Add a row when you add an invariant; add the guard in the same PR.

| Invariant | Enforced in | Guard |
|---|---|---|
| Every change to `orders.state` and every `order_state_transitions` row goes through `db/fsm.py` (`transition_order`, `record_creation`); new orders start in DRAFT or EXTRACTED | `db/fsm.py` | `tests/test_fsm.py` (table matches the published diagram; AST and raw-SQL scan of `app/ agents/ providers/ mcp_servers/ scripts/ db/`) |
| Every database engine is built from a URL that names the psycopg2 driver, including operator-supplied `DATABASE_URL` values | `db/url.py` | `tests/test_db_url.py`; CI boots the image against Postgres |
| Only `/health`, `/ready` and `/static/*` are reachable without credentials when Basic auth is configured | `app/auth.py::UNAUTHENTICATED_PATH_PREFIXES` | `tests/test_security_surfaces.py::TestBasicAuth` |
| Every route that calls the model is behind the per-client rate limit and the daily budget | `app/ratelimit.py::LLM_BACKED_PATH_PREFIXES` | `tests/test_invariants.py::test_every_model_calling_route_is_rate_limited` |
| No response body echoes an exception or a provider message; failures carry a request id | `app/errors.py`, `app/main.py`, `app/middleware.py` | `tests/test_security_surfaces.py::TestErrorSurfaces` |
| Pages carry a nonce-based CSP; templates use no inline event handlers and no `hx-trigger` event filters (both need eval) | `app/middleware.py`, `app/templates/base.html` | `tests/test_invariants.py::test_templates_have_no_inline_handlers_or_eval_filters`; browser tests assert no CSP console errors |
| Synthetic data uses reserved `.test` domains only; the seed is deterministic (fixed Faker seed and clock) | `seed/`, `seed/fixtures/` | `tests/test_fixture_domains.py` |
| Fail-loud: no `ON CONFLICT DO NOTHING`, no silent FK skips, no canned LLM output | `.claude/rules/fail-loud.md` | `tests/test_override_llm.py::test_no_canned_attribute_exists`, per-generator duplicate/missing-FK tests |
