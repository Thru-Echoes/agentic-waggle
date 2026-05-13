# Phase I — Semantic Matcher Summary

**Branch:** `feat/semantic-matcher`
**Final commit:** 777a538

## Status

Phase I structural integration **complete** (Tasks 1–16 of 17). Task 17 (full eval + deploy to Fly + live smoke-test) is **deferred** pending matcher prompt iteration.

## What was built

| Task | Artifact | Commit |
|---|---|---|
| 1 | Migration 004 + DB models (`customer_search_entries` + `sku_search_entries` with pgvector + FTS + CHECK constraints) | d29aec3, 1311ece, 75f86fb |
| 2 | Grounded Pydantic schemas (`CustomerMatch`, `SkuMatch`, candidates, contexts) | 3280f84, d6c51ba, fdb9b53 |
| 3 | Stage A — hybrid retrieve (pgvector cosine + Postgres FTS, RRF fusion + multi-source boost) | 62631a8, 022ba51 |
| 4 | Stage B — LLM disambiguator (gpt-5.4-nano + structured output) | 92c0e43, 1a9c18b |
| 5 | Stage C — public API + per-conversation LRU cache + MatcherIndexEmpty guard | 4b648c3, 61e070c, b93ef75 |
| 6 | `backfill_all` + `reindex_customer` + `reindex_sku` + seed wiring | 806175a |
| 7 | `scripts/boot.py reindex_if_empty` for first-deploy backfill | 471e03c |
| 8 | 277-case test corpus (`tests/fixtures/matcher_corpus.yaml`) — subagent-verified | dceb966, db2a4b0 |
| 9 | Eval harness (`tests/test_matcher_eval.py` + `tests/_eval_helpers.py`) + thresholds | 3040d2d |
| 10 | UI partials (confirm-match, pick-match) + CSS + internal render routes | cd5f28c |
| 11 | `POST /copilot/turns/{id}/confirm-match` route + `resume_with_customer` | a9d26c0 |
| 12 | Orchestrator integration (confidence-graduated branching) | d3a3542 |
| 13 | Order extractor integration | 1cfe002 |
| 14 | Sweep call sites (routes, MCP, demo, inbox-confirm tests) | eb318a1 |
| 15 | Delete legacy resolver/matcher files + remove `rapidfuzz` | 8508632 |
| 16 | Playwright UX tests (scaffolded; execution deferred) | 777a538 |

## Eval results (sample mode, current matcher)

- Customer: 77.1% overall (37/48). 9 categories below threshold:
  - `ambiguous_reference` 33%, `pronoun_resolution` 33%, `casual_colloquial` 67%, `compound_reference` 50%, `possessive` 50%, `site_shortform` 50%, `cross_customer_collision` 67%, `site_reference` 67%, `typo_resolvable` 67%.
- SKU: 83.3% overall (15/18). 3 categories below threshold:
  - `sku_ambiguous` 50%, `sku_colloquial` 67%, `sku_qty_plus_product` 67%.

These are real LLM-quality issues, not harness bugs. Specific failure modes (from `.reports/matcher_eval_customer.md`):
1. Ambiguous queries resolving to a specific customer when they should return null + alternatives — the LLM disambiguator is too eager.
2. Pronoun resolution doesn't actually use `active_account_id` correctly in the prompt.
3. Possessives/typos sometimes fail Stage A retrieval (FTS tokenization misses apostrophe variants).
4. SKU qty+product picks wrong grade variant.

## Task 17 — deferred

The plan requires:
- Step 17.1: full-mode eval must PASS all thresholds → **currently fails**
- Step 17.4: `fly deploy --remote-only` → blocked until 17.1 passes

Deploying a matcher that fails eval thresholds would put known-degraded behavior in production. Defer until iteration is done.

## What to do next

1. **Matcher prompt iteration** — primary focus:
   - Tighten `CUSTOMER_DISAMBIG_SYSTEM` instructions to return `null` more aggressively on ambiguity (current matcher is over-eager).
   - Wire `active_account_name` and `recent_customer_names` more prominently in the user prompt — pronoun cases failing suggests the LLM isn't using the context fields.
   - Add SKU grade-disambiguation guidance (Grade A vs Grade B variants for honey).

2. **Stage A normalization** — `_normalize_for_index_and_query`:
   - Improve apostrophe handling (current possessive failures).
   - Consider lowercase + accent-strip pre-tokenization for the FTS pass.

3. **Re-run eval after each iteration** until all thresholds clear:
   ```bash
   MATCHER_EVAL_FULL=1 pytest tests/test_matcher_eval.py -v
   ```

4. **Then run UI verification** (Task 16 Layer 1 + Layer 2):
   - Layer 2: `pytest tests/test_e2e_copilot_browser.py -v` (5 new tests).
   - Layer 1: dispatch 8 parallel subagents per `.claude/rules/ui-verification.md`.

5. **Then deploy** (Task 17 step 17.4+):
   - PR with eval report deltas, Layer 1 PASS table, Layer 2 counts.
   - `fly deploy --remote-only`.
   - Smoke-test on live: SSE stream should include `match_customer` tool chip; order draft renders with correct `customer_id`.

## Audio dictation spec

Still paused (Section 1 of 5 done in the 2026-05-12 session). Resumes after matcher quality iteration ships.

## Deferred items (from individual task reviews)

- **Task 3:** parametrize empty-query test for whitespace; lower bound on limit-is-top-5 test; PROMPT_VERSION comment; IVFFLAT_PROBES sku-table note; sku_search_entries fixture; canonical_name test seed-determinism flake.
- **Task 4:** N+1 query in `_format_sku_candidate_block`; decompose 180-line `_llm_disambiguate`; SKU defense check parity; negative tests for `LLMConfigurationError` propagation + retry-then-null + defense fallback.
- **Task 5:** lifecycle comment on `_conv_caches`; `SkuContext` Phase II TODO; confidence threshold rationale.
- **Task 9:** consider `MATCHER_EVAL_STRICT=1` env-var gate so CI doesn't fail on aspirational thresholds during iteration.

## TRACE audit goal (this session)

Active session: `trace_20260513_446733`. Will be compared to verbatim Claude Code JSONL transcript at `/Users/echoes/.claude/projects/-Users-echoes-Documents-Contract-agentic-mcp-crm-waggle/a87354ce-fb29-4d7a-ab28-ad71fe404693.jsonl` per the session's audit purpose.
