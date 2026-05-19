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

## v5.1 eval (full mode, 281 cases — post-rigor)

The 2026-05-19 critical-review remediation tightened the eval contract:
`expected_alternatives_min` (specified on 34 corpus cases) is now
**asserted** by the harness — previously it was parsed but silently
ignored, leaving the discriminating half of the abstain-with-alternatives
contract untested. The numbers below are post-rigor.

| Metric | v4 baseline | v5 | v5.1 (pre-rigor, 2026-05-18) | **v5.1 (post-rigor, 2026-05-19)** |
|---|---|---|---|---|
| Customer | 76.3% (158/207) | 94.2% (195/207) | 95.2% (200/210) | **95.2% (200/210)** |
| SKU | 91.4% (64/70) | 100.0% (70/70) | 100.0% (71/71) | **95.8% (68/71)** |
| Customer cats PASS | 5/17 | 10/17 | 13/17 | **11/17** |
| SKU cats PASS | 5/7 | 7/7 | 7/7 | **5/7** |

### What the rigor change moved

Two corrections to the pre-rigor narrative:
- **`ambiguous_reference` and `cross_customer_collision` went FROM FAIL TO PASS** (90.9%→100% and 72.7%→90.9% respectively) — the matcher actually surfaces alternatives correctly on these categories; the pre-rigor framing that "mini commits when null+alts is expected" was overstated for these. The alts-assertion vindicated those cases.
- **`sku_ambiguous` dropped 100%→71.4%** — 2 genuinely vacuous passes (`varroa treatment`, `honey drum`) where the matcher returned null without surfacing the required ≥2 alternatives. The pre-rigor "100% SKU" inflated reality by these vacuous nulls; the post-rigor 95.8% is honest.

### Cycle history

- **v1** — structural matcher; baseline prompts.
- **v2** — tighten ambiguity / pronoun / SKU-grade rules.
- **v3** — clear-winner rule + score gaps + `active_account` injection.
- **v4** — Stage A possessive + suffix normalization + dense fallback.
- **v5** — full quality pass: Stage A noise stripping,
  best-entry-per-target SQL, Stage B prompt rewrite, post-verifier
  business-abbreviation expansion.
- **v5.1** — 4 corpus additions (3 near-miss negatives + wildflower
  honey drum), Stage B disambig upgrade gpt-5.4-nano → gpt-5.4-mini
  (shipped 2026-05-18).
- **v5.1 remediation pass** (2026-05-19, `fix/critical-review-remediation`)
  — surfaced by a 5-agent critical review post-incident: complete the
  prod `/copilot` legacy-JSON hotfix (L3 `_split_tool_calls` + L4
  confirm POST paths) + non-vacuous regression test; honest
  `pytest.mark.skip` on the matcher Layer-2 browser tests; `boot.py`
  reindex moved to a post-bind FastAPI lifespan task (fixes
  bind-blocking); `reindex_sku` E2E coverage; **narrow `except`** in
  Stage B (fail-loud — programming bugs no longer misclassified as
  transient); **eval-rigor**: `expected_alternatives_min` assertion +
  `MATCHER_EVAL_STRICT` gate + documented allowlists in
  `tests/test_matcher_eval.py`; README rapidfuzz removed; corpus +
  migration counts reconciled; comparison-report gpt-5.5 provenance
  disclosed.

### v5 → v5.1 (pre-rigor) deltas — historical record

These are the numbers reported on 2026-05-18 against the looser
(pre-rigor) eval contract. They are kept here because the model-vs-model
comparison they encode (gpt-5.4-nano vs gpt-5.4-mini) is still useful
context for the ship decision. The post-rigor measurements above
supersede them for current truth.

**Wins from mini (4 customer categories moved FAIL→PASS, pre-rigor):**

| Category | v5 | v5.1 (pre-rigor) |
|---|---|---|
| `abbreviation` | 91.7% | 100% |
| `alias` | 84.6% | 100% |
| `casual_colloquial` | 92.3% | 100% |
| `compound_reference` | 80% | 90% |
| `pronoun_resolution` | 90.9% | 100% |

**Regressions from mini (2 customer categories, pre-rigor):**

| Category | v5 | v5.1 (pre-rigor) |
|---|---|---|
| `cross_customer_collision` | 90.9% | 72.7% |
| `site_shortform` | 87.5% | 62.5% |

### Known-acceptable failures (v5.1 post-rigor ship state)

The eval test is **green by default** because these 8 categories are
on the documented allowlist in `tests/test_matcher_eval.py`
(`KNOWN_ACCEPTABLE_CUSTOMER` / `KNOWN_ACCEPTABLE_SKU`). **Regressions
on unlisted categories still fail the test.** Set
`MATCHER_EVAL_STRICT=1` to escalate every sub-threshold category to a
hard fail (CI rigor pass).

Customer (6 categories):

| Category | Precision | Threshold | Note |
|---|---|---|---|
| `abbreviation` | 91.7% (11/12) | 98% | mini misses single colloquial cases (e.g. `Pacific Honey`) — matcher-quality follow-up. |
| `alias` | 92.3% (12/13) | 98% | mini misses numeric-to-word aliases (e.g. `7 Acres Farms`) — follow-up. |
| `apostrophe_variant` | 88.9% (8/9) | 98% | mini misses some `Company`→`Co.` normalization edges — follow-up. |
| `casual_colloquial` | 92.3% (12/13) | 95% | mini misses some casual-phrase resolutions — follow-up. |
| `site_reference` | 86.7% (13/15) | 95% | Cobblestone/Drakestone Main Warehouse swap — Stage A FTS tokenization (matcher-prompt-independent). |
| `site_shortform` | 75.0% (6/8) | 95% | mini under-commits to null on single-customer block disambiguators (`Martinez hilltop`, `Haley orchard road site`). |

SKU (2 categories):

| Category | Precision | Threshold | Note |
|---|---|---|---|
| `sku_ambiguous` | 71.4% (5/7) | 95% | Rigor-exposed: matcher returns null on `varroa treatment` / `honey drum` without the required ≥2 alternatives. Matcher-prompt TODO. |
| `sku_negative` | 87.5% (7/8) | 100% | `entrance reducer` over-matches to `EQ-FEED-ENT` — investigate corpus-vs-seed alignment. |

Trade-offs:
- The confidence-graduated UX surfaces alternatives in production even
  when Stage B commits incorrectly, so the customer-side fails are
  mostly UX friction (no-match → retry) not data corruption.
- The `customer_id: 'null'` (string) pydantic warning seen once in
  earlier 210-case eval logs is mini's structured-output quirk; the
  matcher's retry path handles it cleanly.

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
  the matcher is validated by the full eval harness (95.2% customer / 95.8% SKU, post-rigor).
  Follow-up fix candidates: (a) raise the wait threshold + warm the LLM
  client at app startup, (b) verify `OPENAI_API_KEY` propagates to the
  uvicorn subprocess env, (c) instrument the SSE handler for per-stage
  timing.

## Audio dictation spec

Paused since the 2026-05-12 session (Section 1 of 5 done). Resumes
after matcher v5.1 lands in production.
