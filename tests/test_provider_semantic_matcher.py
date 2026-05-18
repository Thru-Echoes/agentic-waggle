"""Tests for providers/semantic_matcher.py.

Layered: this file grows through Tasks 3-6. Stage A (hybrid retrieve) first,
then Stage B (LLM disambig), then the public API + cache.

Real deps: real pgvector, real sentence-transformers, real Postgres FTS.
LLM tests are added in later tasks and skip cleanly without an OPENAI_API_KEY.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from providers import semantic_matcher
from providers.semantic_matcher import Candidate


@pytest.fixture
def seeded_customer_entries(db_session_with_search_entries):
    """Alias for the conftest fixture; keeps tests self-documenting."""
    return db_session_with_search_entries


# ---------------------------------------------------------------------------
# Stage A — query-time normalization (v5)
# ---------------------------------------------------------------------------
#
# _normalize_for_index_and_query runs on every query before Stage A retrieval.
# v5 cycle 1 added noise-stripping for the most common real-text artifacts:
# subject-line prefixes, polite/scheduling wrappers, casual colloquial
# suffixes, and SKU quantity prefixes.  These tests pin the contract so
# future regex tweaks don't silently drop a pattern that the eval corpus
# depends on.


@pytest.mark.parametrize(
    "raw,expected",
    [
        # v4 carry-overs that must keep working.
        ("O'Reilly Family Orchards", "o reilly family orchards"),
        ("Martinez & Sons Orchards", "martinez sons orchards"),
        ("Haley's", "haley"),
        ("Heartland Apiary Trading Co.", "heartland apiary trading"),
        # v5: subject-line prefixes + suffix-truncate cuts mid-query "Co.".
        (
            "FWD: Heartland Apiary Trading Co. spring contract",
            "heartland apiary trading",
        ),
        # No verb-clause strip (reverted) — trailing noise stays past "co".
        # That's fine because dense+FTS find the entity from "Cascadia Honey Co.".
        (
            "URGENT: Cascadia Honey Co. needs a callback today",
            "cascadia honey",
        ),
        # "Action item:" subject-line strip + suffix-truncate at the mid-query "co".
        (
            "Action item: follow up with Juniper Bee Co. by Thursday",
            "juniper bee",
        ),
        # v5: leading "the " + trailing colloquial noun.
        ("the Martinez folks", "martinez"),
        ("Drakestone guys", "drakestone"),
        ("the Stormchaser mead people", "stormchaser mead"),
        ("those folks at Cobblestone", "cobblestone"),
        ("the Sierra Crest people", "sierra crest"),
        # v5: leading sentence wrappers strip via stop-phrase + casual suffix.
        # Trailing verb/prep clauses are NOT stripped (revert) — these strings
        # are slightly noisy but Stage A handles them via dense retrieval.
        (
            "The Foothill Orchards site needs a full hive inspection.",
            "foothill orchards site needs a full hive inspection",
        ),
        (
            "Check if Urban Hive Pantry has any open orders.",
            "urban hive pantry has any open orders",
        ),
        (
            "Log a note that Old Mission Farm confirmed the pollination quote.",
            "old mission farm confirmed the pollination quote",
        ),
        (
            "We need to invoice Haley Family Orchards for the spring pollination run.",
            "haley family orchards for the spring pollination run",
        ),
        # Casual "account" suffix only strips at END of string — when
        # followed by more text, it stays.
        (
            "The Drakestone Mead account needs a follow-up call.",
            "drakestone mead account needs a follow-up call",
        ),
        # When "account" IS at the end, it's stripped.
        (
            "the Drakestone Mead account",
            "drakestone mead",
        ),
        # Generic-words: "the co." → empty after suffix-token trailing-pop.
        ("the co.", ""),
        # Empty / whitespace handling.
        ("", ""),
        ("   ", ""),
        # Idempotency on already-clean canonical names.
        ("Five Rivers Apiaries", "five rivers apiaries"),
        # Customer-path safety: digit-led aliases must survive unchanged
        # because qty stripping only fires when kind="sku".
        ("7 Acres Farms", "7 acres farms"),
    ],
)
def test_normalize_query(raw: str, expected: str) -> None:
    """v5 normalization strips noise without dropping the entity tokens.

    Default kind=None reproduces what callers see from caches when no
    path-specific stripping is needed (e.g., generic cache key building).
    """
    got = semantic_matcher._normalize_for_index_and_query(raw)
    assert got == expected, f"normalize({raw!r}) -> {got!r}, expected {expected!r}"


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Bare digit: strip just the digit, keep packaging units so the
        # LLM can pick the right size variant.
        ("10 Carniolan mated queens", "carniolan mated queens"),
        ("5 drums of clover honey Grade A", "drums of clover honey grade a"),
        ("2 pails of orange blossom honey", "pails of orange blossom honey"),
        ("20 frames of clover honey", "frames of clover honey"),
        ("3 cases of medium frames", "cases of medium frames"),
        # Mass unit ("100 lb of X"): drop the whole "100 lb of " span.
        ("100 lb of winter fondant", "winter fondant"),
        ("50 lbs of clover honey", "clover honey"),
        ("10 oz of royal jelly", "royal jelly"),
    ],
)
def test_normalize_sku_query(raw: str, expected: str) -> None:
    """SKU-path normalization keeps packaging-unit cues for size variants."""
    got = semantic_matcher._normalize_for_index_and_query(raw, kind="sku")
    assert got == expected, f"normalize({raw!r}, sku) -> {got!r}, expected {expected!r}"


def test_normalize_customer_path_preserves_alias_digits() -> None:
    """Customer-path normalization must not strip digit prefixes from aliases."""
    got = semantic_matcher._normalize_for_index_and_query("7 Acres Farms", kind="customer")
    assert got == "7 acres farms"


@pytest.mark.parametrize(
    "raw,expected",
    [
        # "Limited Partnership" → "lp" but "lp" is NOT in suffix_tokens,
        # so it stays in the normalized form — and that's fine because
        # the indexed canonical "Haley Farms LP" entry has the same "lp"
        # token in its FTS tsv.
        ("Haley Farms Limited Partnership", "haley farms lp"),
        # "Limited Liability Company" → "llc"; "llc" IS in suffix_tokens
        # and gets popped trailing.
        ("ACME Limited Liability Company", "acme"),
        # "Incorporated" → "inc"; "inc" IS in suffix_tokens → popped.
        ("Foothill Incorporated", "foothill"),
        # "Corporation" → "corp"; popped.
        ("Mountain Comb Corporation", "mountain comb"),
        # "Company" → "co"; popped.
        ("North Wind Mead Company", "north wind mead"),
    ],
)
def test_normalize_expands_business_abbreviations(raw: str, expected: str) -> None:
    """Verbose business-suffix forms ('Limited Partnership', 'Incorporated',
    'Corporation', 'Company') get expanded to their abbreviations so they
    match the abbreviated index entries.  Whether the resulting short form
    then gets popped depends on whether it's in the existing suffix_tokens
    set ({co, inc, llc, ltd, company, corp, corporation})."""
    got = semantic_matcher._normalize_for_index_and_query(raw, kind="customer")
    assert got == expected


def test_normalize_preserves_canonical_when_no_noise() -> None:
    """Canonical names with no noise pass through unchanged (after lowercasing)."""
    for canonical in [
        "Foothill Orchards",
        "Blue Ridge Family Orchards",
        "Cascade Orchard Partners",
        "Stormchaser Meadery",
    ]:
        got = semantic_matcher._normalize_for_index_and_query(canonical)
        assert got == canonical.lower()


def test_normalize_iterative_stop_phrase_stripping() -> None:
    """Combos like 'could you please pull up the X' reduce step-by-step."""
    got = semantic_matcher._normalize_for_index_and_query(
        "Could you please pull up the account for Cobblestone Hives"
    )
    assert got == "cobblestone hives"


def test_hybrid_retrieve_returns_canonical_name_for_exact_query(
    seeded_customer_entries,
):
    """'O'Reilly Family Orchards' is a canonical name in seed/customers.py.

    Hybrid retrieve must return that customer in the top-K via both
    dense AND lexical passes.
    """
    candidates = semantic_matcher._hybrid_retrieve(
        "O'Reilly Family Orchards",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    assert len(candidates) > 0, "Empty shortlist for canonical name"
    top = candidates[0]
    assert "O'Reilly" in top.entry_text


def test_hybrid_retrieve_returns_site_for_site_reference(seeded_customer_entries):
    """'OReilly Orchard' is a curated alias for O'Reilly Family Orchards,
    stored in customers.aliases via EDGE_CASE_CUSTOMERS in seed/faker_providers.py.

    backfill_all (Task 6) indexes this alias entry so querying it should
    surface O'Reilly's customer_id in the shortlist.

    Note: the old test-only alias 'OReillys Back 40' was retired in Task 6
    when db_session_with_search_entries switched to calling backfill_all.
    """
    candidates = semantic_matcher._hybrid_retrieve(
        "OReilly Orchard",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    customer_ids = {c.customer_id for c in candidates}
    row = seeded_customer_entries.execute(
        text("SELECT id FROM customers WHERE name LIKE :n"),
        {"n": "%O'Reilly%"},
    ).first()
    assert row is not None, "O'Reilly Family Orchards must exist in the seeded DB"
    assert row.id in customer_ids


def test_hybrid_retrieve_empty_query_returns_empty_list(seeded_customer_entries):
    """Empty query string must return [] without hitting the DB."""
    candidates = semantic_matcher._hybrid_retrieve(
        "",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    assert candidates == []


def test_hybrid_retrieve_limit_is_top_5(seeded_customer_entries):
    """Result list must never exceed TOP_K=5 and must be score-descending."""
    candidates = semantic_matcher._hybrid_retrieve(
        "orchard",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    assert len(candidates) <= 5
    scores = [c.fused_score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_retrieve_best_entry_per_target(seeded_customer_entries):
    """The hybrid retrieve returns at most ONE row per target customer —
    the entry with the highest RRF score for that target.

    The v5 cycle 1.5 SQL switched from "rrf_score + SUM(other entries)"
    (which over-favoured customers with many alias/site entries even
    when they didn't really match the query) to a strict
    `ROW_NUMBER() PARTITION BY target ORDER BY rrf_score DESC, keep=1`
    so each customer's BEST entry represents them.  This invariant is
    important for ambiguity detection and for noisy long-form queries.
    """
    candidates = semantic_matcher._hybrid_retrieve(
        "O'Reilly Back 40",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    assert len(candidates) > 0, "No candidates returned for 'O'Reilly Back 40'"
    by_customer: dict[uuid.UUID | None, list[Candidate]] = {}
    for c in candidates:
        by_customer.setdefault(c.customer_id, []).append(c)
    # Each customer appears at most once now (best-entry-per-target).
    for cust_id, entries in by_customer.items():
        if cust_id is None:
            continue
        assert len(entries) == 1, (
            f"customer {cust_id} appears {len(entries)} times; expected 1 "
            f"(per_target_rank=1 dedupe)"
        )


# ---------------------------------------------------------------------------
# Stage B — LLM disambiguation
# ---------------------------------------------------------------------------

require_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — LLM disambig tests skipped",
)


@require_openai_key
def test_llm_disambiguate_picks_active_account_on_ambiguous_tie(
    seeded_customer_entries,
):
    """When two candidates have similar fused scores, the LLM should
    prefer the active_account_id from conversation context."""
    from agents.schemas import ConversationContext

    candidates = semantic_matcher._hybrid_retrieve(
        "the orchard",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    if len(candidates) < 2:
        pytest.skip("Seed data didn't produce a useful ambiguous shortlist")

    active_id = candidates[1].customer_id  # not the top-scoring one
    ctx = ConversationContext(
        active_account_id=active_id,
        last_3_customer_ids=[active_id] if active_id else [],
    )

    result = semantic_matcher._llm_disambiguate(
        query="the orchard",
        candidates=candidates,
        kind="customer",
        ctx=ctx,
        db=seeded_customer_entries,
    )
    from agents.schemas import CustomerMatch

    assert isinstance(result, CustomerMatch)
    assert result.customer_id == active_id


@require_openai_key
def test_llm_disambiguate_returns_null_on_clear_miss(seeded_customer_entries):
    """For a hint that doesn't plausibly match any candidate, the LLM
    should return customer_id=None."""
    from agents.schemas import ConversationContext, CustomerMatch

    candidates = semantic_matcher._hybrid_retrieve(
        "Italian queens",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    if not candidates:
        pytest.skip("Empty candidate list — degenerate test")

    result = semantic_matcher._llm_disambiguate(
        query="Zephyr Bee Company",
        candidates=candidates,
        kind="customer",
        ctx=ConversationContext(),
        db=seeded_customer_entries,
    )
    assert isinstance(result, CustomerMatch)
    assert result.customer_id is None
    assert result.confidence < 0.5


# ---------------------------------------------------------------------------
# Public API + cache (Stage C)
# ---------------------------------------------------------------------------

@require_openai_key
def test_match_customer_canonical(seeded_customer_entries):
    from agents.schemas import ConversationContext

    match = semantic_matcher.match_customer(
        "O'Reilly Family Orchards",
        conversation_ctx=ConversationContext(),
        conversation_id=uuid.uuid4(),
        db=seeded_customer_entries,
    )
    assert match.customer_id is not None
    assert match.confidence >= 0.80
    assert "O'Reilly" in (match.customer_name or "")


@require_openai_key
def test_match_customer_cache_hit_avoids_second_llm_call(
    seeded_customer_entries, monkeypatch,
):
    """Second call with the same (hint, ctx, conv) hits the cache —
    monkeypatch _llm_disambiguate to assert it's only called once."""
    from agents.schemas import ConversationContext

    call_count = {"n": 0}
    original = semantic_matcher._llm_disambiguate

    def counting(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", counting)
    semantic_matcher.clear_caches()

    conv_id = uuid.uuid4()
    ctx = ConversationContext()
    semantic_matcher.match_customer(
        "O'Reilly", conversation_ctx=ctx, conversation_id=conv_id,
        db=seeded_customer_entries,
    )
    semantic_matcher.match_customer(
        "O'Reilly", conversation_ctx=ctx, conversation_id=conv_id,
        db=seeded_customer_entries,
    )
    assert call_count["n"] == 1, "LLM should have been called only once"


def test_match_customer_empty_hint_returns_null_no_llm_call(
    seeded_customer_entries, monkeypatch,
):
    """Empty hint short-circuits — no LLM call."""
    from agents.schemas import ConversationContext

    def boom(*args, **kwargs):
        raise AssertionError("LLM should not be called for empty hint")

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", boom)
    semantic_matcher.clear_caches()

    match = semantic_matcher.match_customer(
        "", conversation_ctx=ConversationContext(),
        conversation_id=uuid.uuid4(), db=seeded_customer_entries,
    )
    assert match.customer_id is None


def test_cache_lru_evicts_oldest(seeded_customer_entries, monkeypatch):
    """Cache cap is CACHE_CAP_PER_CONV; the (N+1)th distinct hint evicts the oldest."""
    from agents.schemas import CustomerMatch, ConversationContext

    monkeypatch.setattr(semantic_matcher, "CACHE_CAP_PER_CONV", 2)

    # Stub _llm_disambiguate so this test doesn't depend on the LLM.
    def stub_llm(*, query, candidates, kind, ctx, db):
        return CustomerMatch(
            customer_id=None, customer_name=None,
            confidence=0.5, evidence=[query], alternatives=[],
        )

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", stub_llm)
    semantic_matcher.clear_caches()

    conv_id = uuid.uuid4()
    ctx = ConversationContext()
    for hint in ["foo", "bar", "baz"]:  # 3 hints, cap is 2
        semantic_matcher.match_customer(
            hint, conversation_ctx=ctx, conversation_id=conv_id,
            db=seeded_customer_entries,
        )
    cache = semantic_matcher._cache_for(conv_id)
    assert len(cache) == 2


def test_concurrent_calls_idempotent(seeded_customer_entries, monkeypatch):
    """Two threads calling match_customer with the same args produce
    the same answer (idempotent under race)."""
    import concurrent.futures
    from agents.schemas import CustomerMatch, ConversationContext

    # Stub _llm_disambiguate with a deterministic fixed-id result.
    fixed_id = uuid.uuid4()

    def stub_llm(*, query, candidates, kind, ctx, db):
        return CustomerMatch(
            customer_id=fixed_id, customer_name="stub",
            confidence=0.9, evidence=[query], alternatives=[],
        )

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", stub_llm)
    semantic_matcher.clear_caches()

    conv_id = uuid.uuid4()
    ctx = ConversationContext()

    def call():
        return semantic_matcher.match_customer(
            "O'Reilly", conversation_ctx=ctx, conversation_id=conv_id,
            db=seeded_customer_entries,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(call)
        f2 = ex.submit(call)
        r1, r2 = f1.result(), f2.result()
    assert r1.customer_id == r2.customer_id


# ---------------------------------------------------------------------------
# I-1: MatcherIndexEmpty — fail-loud when search-entries table is empty
# ---------------------------------------------------------------------------


def test_match_customer_raises_when_index_empty(db_session_with_seed):
    """customer_search_entries empty → MatcherIndexEmpty raised.

    db_session_with_seed seeds customers/regions/sites but inserts NO
    customer_search_entries rows, so the index-populated guard must fire.
    """
    from agents.schemas import ConversationContext
    from providers.semantic_matcher import MatcherIndexEmpty

    with pytest.raises(MatcherIndexEmpty, match="customer_search_entries is empty"):
        semantic_matcher.match_customer(
            "O'Reilly",
            conversation_ctx=ConversationContext(),
            conversation_id=uuid.uuid4(),
            db=db_session_with_seed,
        )


def test_match_sku_raises_when_index_empty(db_session_with_seed):
    """sku_search_entries empty → MatcherIndexEmpty raised.

    db_session_with_seed inserts no sku_search_entries rows, so the
    index-populated guard must fire before any SKU matching proceeds.
    """
    from agents.schemas import SkuContext
    from providers.semantic_matcher import MatcherIndexEmpty

    with pytest.raises(MatcherIndexEmpty, match="sku_search_entries is empty"):
        semantic_matcher.match_sku(
            "Italian queens",
            sku_context=SkuContext(),
            conversation_id=uuid.uuid4(),
            db=db_session_with_seed,
        )


# ---------------------------------------------------------------------------
# I-2: Truncation — hint > 512 chars is silently trimmed to 512
# ---------------------------------------------------------------------------


def test_match_customer_truncates_long_hint(seeded_customer_entries, monkeypatch, caplog):
    """Hint > 512 chars → truncated to 512 + WARNING logged.

    Passes a 9-char prefix that retrieves real candidates from the seeded
    index ('O\'Reilly ') then pads to 600 chars total. The stub captures
    the query as it arrives at the LLM stage so we can assert length == 512.
    """
    import logging

    from agents.schemas import ConversationContext, CustomerMatch

    captured_hint: dict[str, str | None] = {"value": None}

    def stub_llm(*, query, candidates, kind, ctx, db):
        captured_hint["value"] = query
        return CustomerMatch(
            customer_id=None,
            customer_name=None,
            confidence=0.0,
            evidence=[],
            alternatives=[],
        )

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", stub_llm)
    semantic_matcher.clear_caches()

    # 9-char prefix ensures Stage-A retrieves real candidates so stub is called;
    # total length is 9 + 591 = 600 chars → truncated to 512.
    prefix = "O'Reilly "
    long_hint = prefix + "x" * (600 - len(prefix))
    assert len(long_hint) == 600

    with caplog.at_level(logging.WARNING, logger="providers.semantic_matcher"):
        semantic_matcher.match_customer(
            long_hint,
            conversation_ctx=ConversationContext(),
            conversation_id=uuid.uuid4(),
            db=seeded_customer_entries,
        )

    assert captured_hint["value"] is not None, (
        "stub_llm was never called — Stage-A returned no candidates for the long hint. "
        "The prefix must surface real results from seeded_customer_entries."
    )
    assert len(captured_hint["value"]) == 512
    assert any("Truncating" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# I-3: Exact-SKU short-circuit — confidence=1.0, no LLM call
# ---------------------------------------------------------------------------


def test_match_sku_exact_code_short_circuits_no_llm(seeded_customer_entries, monkeypatch):
    """Exact SKU code → confidence=1.0, NO LLM call, returns product info.

    Inserts a product row and a matching sku_search_entries row directly via
    SQL (the conftest's db_session_with_seed doesn't seed products), then
    passes that product's SKU code to match_sku and confirms the exact-match
    short-circuit fires — i.e., confidence=1.0 and _llm_disambiguate is never
    called.
    """
    from agents.schemas import SkuContext
    from providers.vector_search import VectorSearchProvider

    db = seeded_customer_entries
    test_sku = "TEST-ITQ-NUC5"
    test_name = "Italian Queen, 5-frame nuc"
    product_id = uuid.uuid4()

    # Insert a minimal product row (no FK dependencies on products).
    db.execute(
        text(
            "INSERT INTO products (id, sku, name, category, price_usd, is_regulated) "
            "VALUES (:id, :sku, :name, 'bee_stock', 45.00, false)"
        ),
        {"id": str(product_id), "sku": test_sku, "name": test_name},
    )

    # Insert one sku_search_entries row so _check_index_populated passes.
    client = VectorSearchProvider()
    emb = client.embed(test_name)
    db.execute(
        text(
            "INSERT INTO sku_search_entries "
            "(product_id, entry_text, source, embedding) "
            "VALUES (:pid, :et, 'product_name', CAST(:emb AS vector))"
        ),
        {"pid": str(product_id), "et": test_name, "emb": emb},
    )
    db.commit()

    def boom(*args, **kwargs):
        raise AssertionError("LLM should not be called for an exact SKU code match")

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", boom)
    semantic_matcher.clear_caches()

    match = semantic_matcher.match_sku(
        test_sku,
        sku_context=SkuContext(),
        conversation_id=uuid.uuid4(),
        db=db,
    )
    assert match.confidence == 1.0
    assert match.sku_kind == "product"
    assert match.sku_code == test_sku


# ---------------------------------------------------------------------------
# Backfill + reindex
# ---------------------------------------------------------------------------


def test_backfill_all_is_idempotent(db_session_with_seed):
    """Calling backfill_all twice produces the same final row counts."""
    n1 = semantic_matcher.backfill_all(db_session_with_seed)
    n2 = semantic_matcher.backfill_all(db_session_with_seed)
    assert n1 == n2
    assert n1 > 0


def test_backfill_creates_entries_for_every_customer_and_alias(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    rows = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries WHERE source = 'name'"
    )).scalar_one()
    customer_count = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customers"
    )).scalar_one()
    assert rows == customer_count


def test_backfill_creates_one_entry_per_site(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    rows = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries WHERE source = 'site'"
    )).scalar_one()
    sites = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_sites"
    )).scalar_one()
    assert rows == sites


def test_reindex_customer_only_touches_that_customer(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    cid = db_session_with_seed.execute(text(
        "SELECT id FROM customers LIMIT 1"
    )).scalar_one()
    before_total = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries"
    )).scalar_one()
    semantic_matcher.reindex_customer(cid, db_session_with_seed)
    after_total = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries"
    )).scalar_one()
    assert before_total == after_total  # idempotent — same row count
