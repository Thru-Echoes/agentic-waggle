# Spec — Semantic Matcher (customer + SKU resolution)

**Status:** Draft, ready for implementation-plan handoff.
**Date:** 2026-05-12.
**Authors:** Oliver (direction) + Claude (execution-side design).
**TRACE session:** `trace_20260512_82dc8a`.
**Sibling spec (sequenced after this one):** `docs/superpowers/specs/2026-05-12-audio-dictation-design.md` (paused mid-brainstorm; resumes after this spec ships).

---

## Goal

Replace every fuzzy / rapidfuzz / token-index path that resolves free-text customer or product/service references with a single LLM-grade semantic matcher. The matcher serves the copilot orchestrator (`/copilot`), the inbox confirm flow (`/inbox`), and the order extractor (`agents/order_extractor.py`) — every surface where a user reference needs to land on a DB row.

Pipeline shape: **hybrid retrieve (dense pgvector + Postgres lexical FTS) → LLM disambiguate → Grounded result (id + evidence + confidence + alternatives)**.

## Why

Waggle is positioned as a modern, AI-first agentic CRM. The current pipeline uses an LLM for intent classification and hint extraction (`agents/copilot/intent_router.py`), but the final hint→`customer_id` mapping uses deterministic token/phrase matching (`agents/copilot/account_resolver.py`) with a rapidfuzz fallback (`agents/customer_matcher.py`). The same pattern repeats in `agents/sku_resolver.py` and the `match_customer` stage of `agents/order_extractor.py`. rapidfuzz catches typos but fails on natural-language variants ("the back-40 at O'Reilly", "Mart & Sons", "those orchard folks") — exactly the phrasing a field rep dictating into the copilot is most likely to use.

The principle was stated directly during brainstorming: *"we should be doing everything as a modern, high quality, LLM + AI based app would do."* This spec implements that principle for free-text → row resolution.

## Non-goals

- Multi-tenant deploys (schema is future-compatible; routing is not).
- A/B testing scaffolding for prompts/models.
- Customer-aware SKU bias (e.g., "this customer always orders Italian, not Carniolan").
- Audio dictation (sibling spec).
- Cross-encoder reranking (LLM rerank chosen instead for context-sensitivity; see Architecture).
- Soft-delete behavior on matched rows (forward-compatible note in schema only).

## Architecture

```
chat-turn input (typed or dictated transcript)
         │
         ▼
─────────────────────────────────────────────────────────────────────────
agents/copilot/intent_router.py   (unchanged: LLM extracts target_account_hint)
─────────────────────────────────────────────────────────────────────────
         │
         │  hint = "the back-40 at O'Reilly"
         │  conversation_ctx = ConversationContext(
         │      active_account_id: UUID | None,
         │      last_3_customer_ids: list[UUID],
         │  )
         ▼
─────────────────────────────────────────────────────────────────────────
providers/semantic_matcher.py     (NEW; canonical matcher)
─────────────────────────────────────────────────────────────────────────

  Stage A — Hybrid retrieve (sub-100ms, one SQL statement w/ CTE)

      embed(hint)  via providers/vector_search.py (same all-MiniLM-L6-v2,
                  shared sentence-transformers load)
                         │
       ┌─────────────────┴──────────────────┐
       ▼                                    ▼
    Dense pass                            Lexical pass
    pgvector cosine on                    Postgres FTS:
    customer_search_entries.embedding     plainto_tsquery + ts_rank
    LIMIT 20                              LIMIT 20
       │                                    │
       └────────────► RRF fusion (k=60) ◄───┘
                              │
                              ▼
                     Top-5 candidates with fused score
                     (+ multi-source boost: if 2+ entries from the
                     same customer rank in top-N, that customer's
                     fused score is summed — strong positive signal)

  Stage B — LLM disambiguate (gpt-5.4-nano, ~200ms)

      Prompt template (versioned: PROMPT_VERSION constant in the module):
        "User said: '<hint>'.
         Active account in conversation: <name|null>.
         Recent customers in this conversation: <names|empty>.
         Candidates:
           1. O'Reilly Family Orchards — matched on site 'back-40 yard' (fused 0.91)
           2. O'Connell Brothers — matched on name (fused 0.62)
           3. ...
         Return JSON matching the CustomerMatch schema:
           {customer_id, customer_name, confidence (0..1),
            evidence (1-3 quotes from the user's text),
            alternatives (next-best, up to 4)}.
         If you cannot pick confidently, return customer_id=null with
         alternatives populated.
         If the user clearly means a customer NOT in the candidate list,
         return customer_id=null with low confidence."

      OpenAI structured output (response_format=json_schema bound to CustomerMatch).
      No free-text parsing.

  Stage C — Assemble Grounded result

      CustomerMatch(
          customer_id: UUID | None,
          customer_name: str | None,
          confidence: float,
          evidence: list[str],
          alternatives: list[CustomerCandidate],
      )

  Cache (per-conversation, LRU-bounded, default cap 64 entries):
      key   = (hint_normalized, active_account_id, conversation_id)
      value = CustomerMatch

  Observability (every call, no exceptions):
      - Append a record to assistant ChatTurn.tool_calls JSONB:
          { tool_name: "match_customer",
            input: hint + ctx,
            output: customer_id + confidence + alternatives,
            latency_ms,
            dense_top1_score, fts_top1_score,
            llm_tokens_input, llm_tokens_output, llm_cost_usd,
            prompt_version,
            cache_hit: bool }
      - Emit trace_log_tool_call so audit trail captures matcher
        behavior over time.

         │
         ▼
─────────────────────────────────────────────────────────────────────────
agents/copilot/orchestrator.py    (modified: confidence-graduated UX)
─────────────────────────────────────────────────────────────────────────

  if conf ≥ 0.80 → proceed (existing flow; act on customer_id)

  if 0.50 ≤ conf < 0.80 → partial assistant turn:
       "Logging this for O'Reilly Family Orchards — is that right?"
       [Yes, proceed]  [No, pick another]
       (template: app/templates/partials/copilot_confirm_match.html)
       Resume on Yes via POST /copilot/turns/{id}/confirm-match.

  if conf < 0.50 OR null → partial assistant turn:
       "I couldn't tell which customer. Did you mean:
          • O'Reilly Family Orchards
          • O'Connell Brothers
          • Cascade Orchard Partners
        (Click one, or rephrase your message)"
       (template: app/templates/partials/copilot_pick_match.html)
       Click resumes the paused turn with the picked customer_id.
       Rephrase falls through to a fresh /copilot/turns POST.

  Pending-match state lives on the assistant ChatTurn.tool_calls JSONB as
  a synthetic `pending_customer_match` record (same pattern as
  pending_order_draft / pending_report_draft from Phase F).
```

### Why these architectural choices

- **Hybrid retrieval (dense + lexical) over pure dense:** short text like company names benefits from lexical signal (exact substring is highly diagnostic). Every modern production retrieval system — Pinecone hybrid, Weaviate hybrid, Elasticsearch ELSER, Cohere Rerank — defaults to hybrid. Cost: one extra SQL query per call (combined via CTE).
- **Postgres FTS (`plainto_tsquery` + `ts_rank`), not BM25:** native to Postgres, no extra extensions. `ts_rank` is TF-IDF-style, not pure BM25, but is sufficient for short-text matching at our scale.
- **RRF (Reciprocal Rank Fusion) at k=60:** the simplest, most-cited rank fusion that doesn't require tuning relative dense-vs-lexical weights. Industry default.
- **LLM rerank instead of cross-encoder rerank:** cross-encoders are faster and cheaper per pair, but cannot use conversation context (active_account_id, pronoun resolution, recent-customer recency). Customer matching is context-sensitive; SKU matching less so. For consistency (one matcher, one pattern) we use LLM rerank for both. If SKU latency becomes a problem we'd consider a cross-encoder for the SKU path only.
- **Structured output (Pydantic schema bound to `response_format`):** guarantees the LLM returns a `CustomerMatch` shape — no free-text JSON parsing, no prompt-injection escape hatch on the output side. The schema constrains `customer_id` to UUID; if the LLM picks one not in our shortlist, the matcher discards the choice and falls back to top-RRF with reduced confidence.

## Components

### New files

```
providers/semantic_matcher.py
    Public API:
        match_customer(hint, conversation_ctx, db) -> CustomerMatch
        match_sku(query, sku_context, db) -> SkuMatch
        reindex_customer(customer_id, db) -> None      # called on customer mutations
        reindex_sku(product_id | service_id, db) -> None
        backfill_all(db) -> int                        # idempotent; returns row count
    Internal:
        _hybrid_retrieve(query, entry_table, db) -> list[Candidate]
        _llm_disambiguate(query, candidates, ctx, model, prompt_version)
        _per_conversation_cache: LRU, cap 64 entries per conversation_id
    Constants:
        PROMPT_VERSION = "v1"   # bumped on every prompt change
        RRF_K = 60
        TOP_K = 5
        CACHE_CAP_PER_CONV = 64

providers/semantic_matcher_prompts.py
    Holds the disambiguator prompt templates (customer + SKU).
    Versioned via PROMPT_VERSION. New prompt versions bump the constant
    and add an entry in PROMPT_HISTORY for the eval report.

alembic/versions/004_semantic_matcher.py
    CREATE TABLE customer_search_entries (...)
    CREATE TABLE sku_search_entries (...)
    -- pgvector ivfflat + GIN(tsv) indexes
    -- forward-compat: a commented note in the migration that adding
    -- tenant_id later requires backfill but no schema break

scripts/boot.py     (MODIFIED — see Migration plan)
    Adds a new step "rebuild search indexes if empty" that runs after
    alembic upgrade head and before uvicorn exec. Idempotent: counts
    rows in customer_search_entries; if zero, calls
    semantic_matcher.backfill_all(db). Otherwise skips.

seed/customer_search_entries.py
    Backfill generator for fresh-DB seeding. Same idempotent pattern
    as seed/embeddings.py.

seed/sku_search_entries.py
    Analogous for SKUs.

tests/fixtures/matcher_corpus.yaml
    ~190 customer cases + ~56 SKU cases across 24 categories with
    per-category metadata. See Test corpus section.

tests/test_provider_semantic_matcher.py
    Unit-ish tests:
        - hybrid retrieve returns the expected shortlist for canonical inputs
        - structured-output contract enforced (Pydantic round-trip)
        - cache hits skip the LLM call (verified by mocking the LLM at the
          provider boundary — only place mocking is acceptable, since the
          test is about cache behavior, not LLM behavior)
        - LLM-picks-id-not-in-shortlist path falls back correctly
        - reindex_customer is idempotent
        - concurrency: two concurrent match_customer calls on the same
          conversation+hint produce the same answer (idempotent)
        - perf benchmark: mean < 500ms, P95 < 1s (skipped in CI by
          default; opt-in via env var MATCHER_PERF=1)

tests/test_matcher_eval.py
    The eval harness. Two top-level test functions:
        test_matcher_eval_customer
        test_matcher_eval_sku
    Behavior:
        1. Load tests/fixtures/matcher_corpus.yaml
        2. Filter cases:
           - default: 20% sample per category (deterministic seed=42)
           - if env MATCHER_EVAL_FULL=1: all cases
        3. For each case, call the matcher and record outcome
        4. Compute precision/recall/F1 per category
        5. Assert per-category thresholds (see Test corpus table)
        6. Write .reports/matcher_eval_customer.md and
                 .reports/matcher_eval_sku.md (committed)
        7. Report metadata:
             embedding_model, disambig_model, prompt_version,
             corpus_version (file SHA), git_sha, timestamp,
             eval_mode (sample|full)

app/templates/partials/copilot_confirm_match.html
    Yes/No inline confirm for 0.50 ≤ conf < 0.80.

app/templates/partials/copilot_pick_match.html
    Alternatives picker for conf < 0.50.

app/routes/copilot.py — new route:
    POST /copilot/turns/{turn_id}/confirm-match
        Body (form): customer_id (or "rephrase" sentinel)
        Resumes the paused turn with the human-picked customer_id;
        re-dispatches the original intent (log_report / create_order /
        etc.) with that customer_id committed.
```

### Modified files

```
agents/schemas.py
    + CustomerMatch(Grounded), CustomerCandidate
    + SkuMatch(Grounded),     SkuCandidate
    + ConversationContext (active_account_id, last_3_customer_ids)
    + SkuContext (placeholder; empty for Phase I — keeps API surface
      stable when customer-aware SKU bias arrives in Phase II)

agents/copilot/orchestrator.py
    - Replace AccountIndex / resolve_account import with
      semantic_matcher.match_customer
    - Add confidence-graduated branching (proceed / inline-confirm /
      disambiguate)
    - Append matcher tool_calls record to assistant ChatTurn (Observability)

agents/order_extractor.py
    - match_customer stage → semantic_matcher.match_customer
    - resolve_skus stage → semantic_matcher.match_sku (per line item)
    - Remove imports: `find_customer_match`, `_load_catalog`,
      `resolve_line_item`, `resolve_via_llm`
    - Grounded evidence + confidence flows through the existing
      evidence-propagation pipeline (per commit 84d0a88)

app/routes/copilot.py
    - Remove imports of AccountIndex / build_account_index / resolve_account
    - Replace the route-level `resolve_account` call (line ~1091)
      with semantic_matcher.match_customer
    - Drop the AccountIndex caching layer (matcher owns its own cache)

mcp_servers/sales_copilot_server.py
    - Remove `from agents.customer_matcher import find_customer_match`
    - Either route through semantic_matcher.match_customer or accept
      that the MCP tool surface degrades to ID-only lookup (decide
      during implementation — find_customer_match was a name-search MCP
      tool; replacing with semantic_matcher.match_customer preserves
      the tool's external contract)

scripts/demo.py
    - Remove `from agents.copilot.account_resolver import build_account_index`
    - The demo's step that referenced AccountIndex either calls
      semantic_matcher.match_customer directly or drops that call
      (it was diagnostic; not load-bearing for the demo flow)

agents/sku_resolver.py
    [DELETED — replaced by semantic_matcher.match_sku]

agents/copilot/account_resolver.py
    [DELETED — replaced by semantic_matcher.match_customer]

agents/customer_matcher.py
    [DELETED — replaced by semantic_matcher.match_customer]
    The `normalize_name` helper this file exports is reimplemented as
    a private `_normalize_for_index_and_query` inside the new provider.
    Audited call sites: only `account_resolver.py` and
    `test_agent_copilot_account_resolver.py` consume it; both are
    deleted/rewritten.

db/models.py
    + CustomerSearchEntry SQLModel
    + SkuSearchEntry SQLModel
    + relationship from Customer to its CustomerSearchEntries
    + relationship from Product/Service to its SkuSearchEntries

tests/schema.sql
    Append the two new tables' DDL (matching alembic 004).

seed/embeddings.py
    + Backfill step that calls semantic_matcher.backfill_all(db)
      after customers/products/services + their reports are seeded.

requirements.txt
    - Remove rapidfuzz (no longer used in primary paths)
```

### Deleted (no half-measures)

```
agents/copilot/account_resolver.py              (replaced)
agents/customer_matcher.py                       (replaced)
agents/sku_resolver.py                           (replaced)
tests/test_agent_copilot_account_resolver.py     (rewritten)
tests/test_agent_customer_matcher.py             (rewritten)
tests/test_agent_sku_resolver.py                 (rewritten)
requirements.txt: rapidfuzz                      (no remaining primary use)
```

**Full call-site audit** (grep-verified — every import of the to-be-deleted modules):

| File | Imports today | Migration |
|---|---|---|
| `agents/copilot/orchestrator.py` | `AccountIndex`, `resolve_account` | replace with `semantic_matcher.match_customer` |
| `agents/order_extractor.py` | `find_customer_match`, sku_resolver internals | replace with matcher calls |
| `agents/copilot/account_resolver.py` | `normalize_name` from customer_matcher | file deleted |
| `app/routes/copilot.py` | `AccountIndex`, `build_account_index`, `resolve_account` | replace + drop AccountIndex cache layer |
| `mcp_servers/sales_copilot_server.py` | `find_customer_match` | route through matcher or document contract change |
| `scripts/demo.py` | `build_account_index` | remove (was diagnostic) |
| `tests/test_agent_copilot_account_resolver.py` | several | file deleted |
| `tests/test_agent_customer_matcher.py` | several | file deleted |
| `tests/test_agent_sku_resolver.py` | `FUZZY_SCORE_MIN`, `resolve_line_item` | file deleted |
| `tests/test_agent_copilot_context_builder.py` | `build_account_index` | rewrite to use new matcher |
| `tests/test_agent_copilot_orchestrator.py` | `build_account_index` | rewrite to use new matcher |

## Schema

### `customer_search_entries`

```sql
CREATE TABLE customer_search_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    entry_text  TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('name','alias','site')),
    embedding   VECTOR(384) NOT NULL,
    tsv         TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', entry_text)) STORED,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    -- Future multi-tenant: ADD COLUMN tenant_id UUID;
    --                       backfill from customers.tenant_id;
    --                       add index on (tenant_id, ...).
    -- Future soft-delete: queries already filter via JOIN to customers;
    --                     when customers.deleted_at is added, JOIN
    --                     gains WHERE customers.deleted_at IS NULL.
);
CREATE INDEX customer_search_entries_embedding_ivfflat
    ON customer_search_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 32);
CREATE INDEX customer_search_entries_tsv_gin
    ON customer_search_entries USING gin (tsv);
CREATE INDEX customer_search_entries_customer_id
    ON customer_search_entries (customer_id);
```

### `sku_search_entries`

```sql
CREATE TABLE sku_search_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NULL REFERENCES products(id) ON DELETE CASCADE,
    service_id  UUID NULL REFERENCES services(id) ON DELETE CASCADE,
    entry_text  TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN (
                  'product_name','product_code','product_alias',
                  'service_name','service_code','service_alias'
                )),
    embedding   VECTOR(384) NOT NULL,
    tsv         TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', entry_text)) STORED,
    -- exactly one of product_id / service_id must be non-null
    CHECK ((product_id IS NULL) != (service_id IS NULL)),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sku_search_entries_embedding_ivfflat
    ON sku_search_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 16);
CREATE INDEX sku_search_entries_tsv_gin
    ON sku_search_entries USING gin (tsv);
CREATE INDEX sku_search_entries_product_id
    ON sku_search_entries (product_id);
CREATE INDEX sku_search_entries_service_id
    ON sku_search_entries (service_id);
```

## Test corpus + eval harness

The matcher's spec is its test corpus. The test corpus is its spec.

### Fixture file shape — `tests/fixtures/matcher_corpus.yaml`

```yaml
# Each case: input_text, expected, category, optional conversation context.
# Customer references use names (resolved to UUIDs at load-time from
# seed/customers.py + seed/sites.py — so corpus survives re-seeds.)

- input: "O'Reilly Family Orchards"
  expected_customer_name: "O'Reilly Family Orchards"
  category: canonical

- input: "the back-40 at O'Reilly"
  expected_customer_name: "O'Reilly Family Orchards"
  category: site_reference

- input: "they're still showing varroa"
  expected_customer_name: "O'Reilly Family Orchards"
  category: pronoun_resolution
  context:
    active_account_name: "O'Reilly Family Orchards"
    last_3_customer_names: ["O'Reilly Family Orchards"]

- input: "the orchard"
  expected_customer_name: null
  expected_alternatives_min: 2
  category: ambiguous_reference

- input: "Zephyr Bee Company"
  expected_customer_name: null
  category: negative_case
```

### Customer categories, case counts, eval thresholds

| # | Category | Min cases | Threshold | What it catches |
|---|---|---|---|---|
| 1 | canonical | 10 | **100%** | exact full names; baseline sanity |
| 2 | abbreviation | 10 | ≥98% | "O'Reilly" → O'Reilly Family Orchards |
| 3 | possessive | 8 | ≥98% | "O'Reilly's" with apostrophe-s |
| 4 | alias | 10 | ≥98% | exact `customers.aliases` entry |
| 5 | apostrophe_variant | 6 | ≥98% | "OReilly", "O Reilly", "O'reilly" |
| 6 | site_reference | 12 | ≥95% | "the back-40" → owner customer |
| 7 | site_shortform | 8 | ≥95% | "back-40 at O'Reilly's" |
| 8 | typo_resolvable | 10 | ≥90% | "Oreily", "Martnez & Sons" |
| 9 | long_form_sentence | 15 | ≥95% | realistic field-rep dictation |
| 10 | pronoun_resolution | 10 | ≥85% | "they/it/the customer" + context |
| 11 | cross_customer_collision | 10 | ≥90% | shared token, picked by context |
| 12 | ambiguous_reference | 10 | ≥95% | returns null OR alternatives — NOT confident wrong |
| 13 | negative_case | 10 | **100%** | unknown customer → null |
| 14 | generic_words | 8 | **100%** | "the company", "our customer" → null |
| 15 | compound_reference | 8 | ≥85% | multiple customers in one sentence |
| 16 | casual_colloquial | 8 | ≥95% | "the O'Reilly folks" |
| 17 | partial_match_noise | 6 | ≥95% | common word, no customer |

### SKU categories, case counts, eval thresholds

| Category | Min cases | Threshold |
|---|---|---|
| sku_canonical_code | 10 | **100%** |
| sku_product_name | 10 | ≥98% |
| sku_qty_plus_product | 10 | ≥98% |
| sku_service | 6 | ≥95% |
| sku_colloquial | 8 | ≥90% |
| sku_ambiguous | 6 | ≥95% null-or-alternatives |
| sku_negative | 6 | **100%** null |

**Corpus total:** ~190 customer cases + ~56 SKU cases = ~246 fixture rows across 24 categories.

### Eval harness behavior

```python
# tests/test_matcher_eval.py — sketch

EVAL_MODE = os.environ.get("MATCHER_EVAL_MODE", "sample")  # 'sample' | 'full'
SAMPLE_FRACTION_PER_CATEGORY = 0.20

def test_matcher_eval_customer():
    corpus = load_corpus("tests/fixtures/matcher_corpus.yaml", kind="customer")
    if EVAL_MODE == "sample":
        corpus = stratified_sample(corpus, SAMPLE_FRACTION_PER_CATEGORY, seed=42)

    per_category = run_all_cases(corpus, semantic_matcher.match_customer)
    report = compute_metrics(per_category)

    write_report(
        report,
        path=".reports/matcher_eval_customer.md",
        metadata={
            "embedding_model": "all-MiniLM-L6-v2",
            "disambig_model": settings.copilot_matcher_disambig_model,
            "prompt_version": semantic_matcher.PROMPT_VERSION,
            "corpus_sha": sha256(corpus_yaml_bytes),
            "git_sha": resolve_git_sha(),
            "timestamp": utc_now().isoformat(),
            "eval_mode": EVAL_MODE,
        },
    )

    for cat, threshold in CUSTOMER_THRESHOLDS.items():
        actual = report[cat]["precision"]
        assert actual >= threshold, (
            f"{cat}: {actual:.2%} < threshold {threshold:.2%}\n"
            f"Failed cases:\n{format_failures(report[cat])}"
        )
```

Default CI runs `MATCHER_EVAL_MODE=sample` (~50 customer + ~12 SKU cases, ~$0.003/run). Full eval (`MATCHER_EVAL_FULL=1` or `MATCHER_EVAL_MODE=full`) runs nightly via cron and on PRs labeled `eval:full`.

### Subagent verification pass (mandatory before merge)

After the corpus YAML is drafted, dispatch an Explore subagent with the fixture, the seed data (`seed/customers.py`, `seed/sites.py`, `seed/products.py`, `seed/services.py`), and this prompt:

> "Are these 246 cases genuinely thorough and realistic for a commercial-beekeeping field rep dictating into an agentic copilot? Identify: (a) categories of variation present in real speech but missing from the corpus, (b) any cases that are filler / tautological / too easy, (c) cases that reference customers, sites, products, or services not present in the seed data, (d) whether each category threshold is calibrated correctly — too lax (lets regressions slip) or too strict (false-positive CI failures). Return a structured list of gaps + concrete recommendations."

Iterate the corpus until the subagent confirms thoroughness. Re-run the eval harness after every change. Commit the subagent's final report to `.reports/matcher_corpus_review.md`.

## Error handling and edge cases

| # | Failure | Matcher behavior | Why |
|---|---|---|---|
| 1 | search-entries table empty | Raise `MatcherIndexEmpty` | Deployment bug — fail loud. boot.py should have backfilled. |
| 2 | `OPENAI_API_KEY` missing at init | Raise `LLMConfigurationError` | Matches `providers/llm.py` pattern |
| 3 | Embedding generation fails | Raise; don't fall back to FTS-only | Silent degradation is worse than loud failure |
| 4 | FTS query parse error (rare) | Log warning + fall back to dense-only | Documented degradation, not silent failure |
| 5 | LLM returns malformed JSON | Structured output guarantees schema; on network glitch retry 1×, then return null + low conf + log | Don't crash the turn |
| 6 | LLM rate-limited | Exponential backoff, 3 attempts, then raise | Matches existing LLM retry policy |
| 7 | LLM timeout | Raise (no fake null) | Honest reporting |
| 8 | Empty/whitespace hint | Return null immediately, no LLM call | Pure function, save the cost |
| 9 | Hint > 512 chars | Truncate before embedding + log | Embedding model context limit |
| 10 | Hint is a literal UUID | Bypass matching, direct lookup | Optimization |
| 11 | active_account_id references deleted customer | Ignore, treat as no active context | Stale state shouldn't poison match |
| 12 | Hybrid retrieve returns 0 from both passes | Return null + empty alternatives, no LLM call | No list to disambiguate |
| 13 | LLM picks customer_id not in shortlist | Discard LLM choice; fall back to top-RRF with confidence × 0.5; log | Defense against prompt-engineering failures |
| 14 | Multiple sources match same customer | Boost that customer's RRF score before LLM disambig | Positive signal |
| 15 | New customer added mid-conversation | Per-conversation cache won't see it until restart | Documented limitation |

## Confidence-graduated UX

```
confidence ≥ 0.80
    └─► proceed (existing flow; act on customer_id)

0.50 ≤ confidence < 0.80
    └─► partial assistant turn renders copilot_confirm_match.html:
        "Logging this for O'Reilly Family Orchards — is that right?"
        [Yes, proceed]  [No, pick another]
        Yes → POST /copilot/turns/{id}/confirm-match → resume
        No  → swap in copilot_pick_match.html

confidence < 0.50 OR null
    └─► partial assistant turn renders copilot_pick_match.html:
        "I couldn't tell which customer. Did you mean:
          • O'Reilly Family Orchards
          • O'Connell Brothers
          • Cascade Orchard Partners
         (Click one, or rephrase your message.)"
        Click → POST /copilot/turns/{id}/confirm-match?customer_id=...
        Rephrase → falls through to a fresh /copilot/turns POST
```

Pending-match state on assistant `ChatTurn.tool_calls` JSONB as a synthetic `pending_customer_match` record (same shape as `pending_order_draft` / `pending_report_draft` from Phase F).

## Observability

Every matcher call, no exceptions, produces two artifacts:

1. **chat_turns.tool_calls record** appended to the assistant `ChatTurn` for the turn that called the matcher:
   ```json
   {
     "tool_name": "match_customer" | "match_sku",
     "input": {"hint": "...", "active_account_id": "...", "...": "..."},
     "output": {"customer_id": "...", "confidence": 0.91, "alternatives": [...]},
     "latency_ms": 234,
     "dense_top1_score": 0.91,
     "fts_top1_score": 0.78,
     "fused_top1_score": 0.0204,
     "llm_tokens_input": 312,
     "llm_tokens_output": 84,
     "llm_cost_usd": 0.000035,
     "prompt_version": "v1",
     "cache_hit": false
   }
   ```
2. **TRACE `trace_log_tool_call`** so the audit trail captures matcher behavior over time. `server="semantic_matcher"`, `tool_name="match_customer"`, input + output as above. Required by the project's TRACE protocol; consistent with how the orchestrator's other tools (`lookup_customer`, `search_customer_reports`, etc.) are logged.

Both artifacts surface in the existing `/copilot` UI: the tool chip in the assistant bubble shows the matcher chip, latency, and the resolved customer name; the evidence expander shows the matcher's evidence quotes.

Eval reports (`.reports/matcher_eval_customer.md` and `.reports/matcher_eval_sku.md`) include the metadata block (embedding model, disambig model, prompt version, corpus SHA, git SHA, timestamp, eval mode) so any regression is traceable to a specific change.

## Migration plan

1. **Implementation order:**
   1. Schema + migration 004 + DB models + tests/schema.sql
   2. `providers/semantic_matcher.py` core + tests/test_provider_semantic_matcher.py
   3. Corpus fixture file (draft from real seed data) + tests/test_matcher_eval.py
   4. Subagent verification pass on the corpus; iterate
   5. `seed/embeddings.py` backfill + `scripts/boot.py` reindex-if-empty step
   6. `agents/copilot/orchestrator.py` integration + confidence-graduated UX templates + new route
   7. `agents/order_extractor.py` integration
   8. `tests/test_e2e_copilot_browser.py` extensions (Yes/No/Pick/Rephrase paths)
   9. UI verification per `.claude/rules/ui-verification.md` (Layer 1 + Layer 2)
   10. Delete `account_resolver.py`, `customer_matcher.py`, `sku_resolver.py`; remove rapidfuzz from requirements
   11. Re-validate `tests/test_order_extractor*.py` — expect 5–20% of asserted outcomes to shift as the new matcher disagrees with rapidfuzz on edge cases. Update assertions to the new (verified-correct) outputs. **This is real work, not cleanup.**
   12. Full `make test` against fresh DB; full Playwright run; full eval (`MATCHER_EVAL_FULL=1`)

2. **First Fly deploy after this lands:**
   - `boot.py` runs `alembic upgrade head` → migration 004 creates empty tables
   - `boot.py` reindex-if-empty step runs `semantic_matcher.backfill_all(db)` — populates entries from existing customers/sites/products/services. ~40 customers + sites + 64 SKUs ≈ ~250 embeddings; takes ~3s
   - uvicorn starts; first matcher call hits a populated index
   - Smoke-test on live: `POST /copilot/turns` with "draft an order for Martinez" → matcher resolves correctly; visible in tool chip + TRACE audit trail
   - Eval on live: not run by default (CI handles it); can be invoked manually via `fly ssh console -C "MATCHER_EVAL_FULL=1 pytest tests/test_matcher_eval.py"` if needed

3. **Rollback plan** (in case matcher quality is unacceptable post-merge):
   - Revert the orchestrator + order-extractor call-site changes (single commit revert)
   - The new tables remain in the DB (no data migration needed for rollback)
   - The new provider becomes dormant
   - Re-add `account_resolver.py` / `customer_matcher.py` / `sku_resolver.py` from git history if needed
   - No user data is lost (no chat-turn or order-row schema change)

## Known limitations (Phase I)

- **N+1 LLM latency in order extraction:** a 10-line-item order produces 10 separate `match_sku` calls → 10 LLM disambig calls → ~2s added to order extraction. Acceptable for our scale (orders are extracted asynchronously off the user's critical path). Future optimization: batched SKU disambig ("here are 10 line items, resolve all in one LLM call"). Tracked.
- **No mid-conversation customer-add visibility:** if a customer is added while a user has an open conversation, the per-conversation cache won't surface them until conversation restart. Documented; rare.
- **LLM non-determinism in eval:** even with `temperature=0`, the OpenAI API can produce mildly different outputs across runs. Threshold-tuning policy: if a category fails a single eval run, re-run once; if it fails twice, investigate (probably means the prompt or model genuinely drifted). Never relax a threshold below 0.70 without a written reason in the commit message.
- **Postgres FTS is not BM25:** `ts_rank` is TF-IDF-style. For our text scale (~250 short entries) this is functionally equivalent. If we ever need true BM25 we'd install the `pg_search` extension; not blocking.
- **No tenant scoping:** schema is forward-compatible (commented in migration) but routing is not. Adding a tenant column later requires backfill + index changes but no schema break.
- **LLM rerank over cross-encoder rerank:** chosen for context-sensitivity. If SKU latency becomes a hot path issue, swap in a cross-encoder for the SKU path while keeping LLM rerank for customers (where pronoun resolution matters).

## Acceptance criteria

The spec is implemented when:

1. `providers/semantic_matcher.py` exists with the public API listed above and passes `pyright` clean.
2. Migration 004 applies cleanly on a fresh DB and on the existing live DB; `boot.py` backfills any empty index automatically.
3. `tests/fixtures/matcher_corpus.yaml` exists with ≥246 cases across the 24 categories, and the subagent verification report (`.reports/matcher_corpus_review.md`) confirms thoroughness.
4. `tests/test_matcher_eval.py` passes in both `sample` and `full` modes; produces the two `.reports/*.md` artifacts.
5. `tests/test_provider_semantic_matcher.py` covers: hybrid retrieve shape, structured-output contract, cache idempotency, LLM-out-of-shortlist fallback, reindex idempotency, concurrency idempotency, perf benchmark (opt-in).
6. `agents/copilot/orchestrator.py` uses `semantic_matcher.match_customer` and renders the confidence-graduated UX. `POST /copilot/turns/{id}/confirm-match` resumes the paused turn correctly.
7. `agents/order_extractor.py` uses `semantic_matcher.match_customer` + `.match_sku`; the existing E2E order-extraction suite passes (with updated assertions where matcher behavior intentionally differs from rapidfuzz).
8. UI verification per `.claude/rules/ui-verification.md`: Layer 1 subagent walks on the new confirm-match and pick-match elements (PASS table in the PR description); Layer 2 Playwright tests cover Yes/No/Pick/Rephrase + double-click + back-button paths.
9. `account_resolver.py`, `customer_matcher.py`, `sku_resolver.py` are deleted. `rapidfuzz` is removed from `requirements.txt`. The following greps all return zero hits:
   - `grep -rn "rapidfuzz" agents/ providers/ app/ mcp_servers/ scripts/` (exclude test/)
   - `grep -rn "account_resolver\|customer_matcher\|sku_resolver" agents/ providers/ app/ mcp_servers/ scripts/` (exclude test/ and the deleted modules themselves)
10. Observability artifacts visible: matcher tool-call chip rendered in `/copilot` UI; `chat_turns.tool_calls` records present; TRACE audit trail captures matcher calls.
11. Full `make test` passes against fresh DB: target ~340 tests (up from 310 in Phase H), 0 failures.
12. PR description includes: matcher eval report deltas vs prior baseline, per-category accuracy table, list of order-extractor tests whose assertions changed (with rationale).

## Open questions to confirm before plan handoff

None — all design decisions in this spec have been agreed during the brainstorm. The implementation plan should treat this spec as authoritative.
