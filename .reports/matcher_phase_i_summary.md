# Phase I — Semantic Matcher

**Branch:** `feat/semantic-matcher`
**Status:** SHIPPED at v5.1 (2026-05-14)

## What shipped

LLM-driven semantic matching replaces all legacy rapidfuzz / Levenshtein
paths in the customer + SKU resolution pipeline. Three-stage architecture:

- **Stage A** — hybrid retrieve (pgvector cosine + Postgres FTS, RRF
  fusion + best-entry-per-target SQL via `ROW_NUMBER PARTITION BY target`).
- **Stage B** — LLM disambiguator with structured output
  (gpt-5.4-mini, upgraded from gpt-5.4-nano in v5.1).
- **Stage C** — public API (`match_customer`, `match_sku`) +
  per-conversation LRU cache + `MatcherIndexEmpty` guard.

The UI ships a confidence-graduated confirmation flow
(auto-accept / confirm / pick-from-alts) via
`POST /copilot/turns/{id}/confirm-match`. Order extractor and copilot
orchestrator both go through the public API. `rapidfuzz` removed.
`customer_search_entries` and `sku_search_entries` tables added in
migration 004 with pgvector + FTS + CHECK constraints.
`scripts/boot.py reindex_if_empty` backfills the index on first deploy.

## v5.1 eval (full mode, 281 cases)

| Metric | v4 baseline | v5 | v5.1 |
|---|---|---|---|
| Customer | 76.3% (158/207) | 94.2% (195/207) | **95.2% (200/210)** |
| SKU | 91.4% (64/70) | 100.0% (70/70) | **100.0% (71/71)** |
| Customer categories PASS | 5/17 | 10/17 | **13/17** |
| SKU categories PASS | 5/7 | 7/7 | **7/7** |

### Cycle history

- **v1** — structural matcher; baseline prompts.
- **v2** — tighten ambiguity / pronoun / SKU-grade rules.
- **v3** — clear-winner rule + score gaps + `active_account` injection.
- **v4** — Stage A possessive + suffix normalization + dense fallback.
- **v5** — full quality pass: Stage A noise stripping,
  best-entry-per-target SQL, Stage B prompt rewrite, post-verifier
  business-abbreviation expansion.
- **v5.1** — 4 corpus additions (3 near-miss negatives + wildflower
  honey drum), Stage B disambig upgrade gpt-5.4-nano → gpt-5.4-mini.

### v5 → v5.1 deltas

**Wins from mini (4 customer categories moved from FAIL to PASS):**

| Category | v5 | v5.1 |
|---|---|---|
| `abbreviation` | 91.7% | **100%** |
| `alias` | 84.6% | **100%** |
| `casual_colloquial` | 92.3% | **100%** |
| `compound_reference` | 80% | **90%** |
| `pronoun_resolution` | 90.9% | **100%** |

**Regressions from mini (2 customer categories moved further from threshold):**

| Category | v5 | v5.1 | Regression |
|---|---|---|---|
| `cross_customer_collision` | 90.9% | **72.7%** | −18.2pp |
| `site_shortform` | 87.5% | **62.5%** | −25.0pp |

### Known-acceptable failures (v5.1 ship state)

| Category | Precision | Threshold | Note |
|---|---|---|---|
| `cross_customer_collision` | 72.7% | 90% | mini commits to a specific name on prefix collisions (`Riverbend`, `Wenatchee`, `Seven Acres Farms`) when null+alternatives is expected. |
| `site_shortform` | 62.5% | 95% | mini under-commits to null on `Martinez hilltop`, `Foothill river block`, `Haley orchard road site`. |
| `ambiguous_reference` | 90.9% | 95% | `Cascade` over-resolves to Cascade Orchard Partners. |
| `site_reference` | 86.7% | 95% | Cobblestone/Drakestone Main Warehouse swap (Stage A FTS tokenization). |

Trade-off accepted: mini's gains across abbreviation / alias / casual /
compound / pronoun outweigh the collision/shortform regressions in
aggregate, and the confidence-graduated UX surfaces alternatives in
production even when Stage B commits incorrectly. The
`customer_id: 'null'` (string) pydantic warning seen once in the
210-case eval log is mini's structured-output quirk; the matcher's
retry path handles it cleanly.

## Tasks 1–17 — final state

| Task | Artifact | Status |
|---|---|---|
| 1 | Migration 004 + DB models (`customer_search_entries` + `sku_search_entries` with pgvector + FTS + CHECK) | shipped |
| 2 | Grounded Pydantic schemas (`CustomerMatch`, `SkuMatch`, candidates, contexts) | shipped |
| 3 | Stage A — hybrid retrieve | shipped |
| 4 | Stage B — LLM disambiguator | shipped (v5.1: model = gpt-5.4-mini) |
| 5 | Stage C — public API + LRU cache + `MatcherIndexEmpty` guard | shipped |
| 6 | `backfill_all` + `reindex_customer` + `reindex_sku` + seed wiring | shipped |
| 7 | `scripts/boot.py reindex_if_empty` for first-deploy backfill | shipped |
| 8 | 281-case test corpus | shipped (v5.1: +4 cases — 3 near-miss negatives + wildflower honey drum) |
| 9 | Eval harness | shipped |
| 10 | UI partials (confirm-match, pick-match) + CSS + internal render routes | shipped |
| 11 | `POST /copilot/turns/{id}/confirm-match` + `resume_with_customer` | shipped |
| 12 | Orchestrator integration (confidence-graduated branching) | shipped |
| 13 | Order extractor integration | shipped |
| 14 | Sweep call sites (routes, MCP, demo, inbox-confirm tests) | shipped |
| 15 | Delete legacy resolver/matcher + remove `rapidfuzz` | shipped |
| 16 | Playwright UX tests | partial — fixture matcher-index backfill fixed; SSE-timeout deferred (see below) |
| 17 | Full eval + fly deploy | eval ✓; deployed at v5.1 |

## Deferred items (post-merge follow-ups)

- **Task 3 review** — parametrize empty-query test for whitespace;
  lower-bound on limit-is-top-5 test; PROMPT_VERSION comment;
  `IVFFLAT_PROBES` sku-table note; `sku_search_entries` fixture;
  `canonical_name` test seed-determinism flake.
- **Task 4 review** — N+1 query in `_format_sku_candidate_block`;
  decompose 180-line `_llm_disambiguate`; SKU defense check parity;
  negative tests for `LLMConfigurationError` propagation +
  retry-then-null + defense fallback.
- **Task 5 review** — lifecycle comment on `_conv_caches`;
  `SkuContext` Phase II TODO; confidence threshold rationale.
- **Task 9 review** — `MATCHER_EVAL_STRICT=1` env-var gate so CI
  doesn't fail on aspirational thresholds during iteration.
- **Stage A FTS tokenization fix** — Cobblestone ↔ Drakestone Main
  Warehouse swap suggests "Main Warehouse" generic terms are
  dominating the FTS rank.
- **Prompt iteration for mini's collision-commit bias** — cycle 4
  candidate; add explicit "if 2+ candidates share a prefix token,
  prefer null+alternatives" rule and re-eval.
- **Combined customer+SKU corpus cases** — the current corpus model
  asserts customer-OR-sku, not both. Schema extension would enable
  testing the canonical "20 Italian queens for Martinez" utterance
  end-to-end.
- **Layer 2 Playwright SSE-timeout (blocker for full Layer 2)** — the
  browser-test fixtures now backfill `customer_search_entries` /
  `sku_search_entries` (fixed in this PR), but `test_e2e_copilot_browser`
  still times out waiting for `.turn.assistant` to render within 60s
  after a turn submission. The shell tests pass (page load, empty state,
  rail); the failure is in the SSE/orchestrator chain (router + main +
  matcher = 3 sequential LLM calls/turn) — not a v5.1 regression (these
  tests were scaffolded in Phase I Task 16 and never executed to green).
  `make test` (318 passed) covers the non-Playwright integration paths;
  the matcher is validated by the full eval harness (95.2% / 100%).
  Follow-up fix candidates: (a) raise the wait threshold + warm the LLM
  client at app startup, (b) verify `OPENAI_API_KEY` propagates to the
  uvicorn subprocess env, (c) instrument the SSE handler for per-stage
  timing.

## Audio dictation spec

Paused since the 2026-05-12 session (Section 1 of 5 done). Resumes
after matcher v5.1 lands in production.
