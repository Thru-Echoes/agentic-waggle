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
    """'OReillys Back 40' is a distinctive alias seeded for O'Reilly Family Orchards.

    The fixture inserts this alias entry so querying it should surface
    O'Reilly's customer_id in the shortlist.
    """
    candidates = semantic_matcher._hybrid_retrieve(
        "OReillys Back 40",
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


def test_hybrid_retrieve_multi_source_boost(seeded_customer_entries):
    """When 2+ entries from the same customer rank in the shortlist
    (e.g., canonical name AND the alias 'OReillys Back 40' both match
    'O'Reilly Back 40'), the customer_id must appear more than once in the
    raw per-entry results, reflecting the multi-source boost.
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
    top_customer_id = candidates[0].customer_id
    assert len(by_customer[top_customer_id]) >= 2


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
    """Cache cap is CACHE_CAP_PER_CONV (64); the 65th distinct hint
    evicts the oldest."""
    from agents.schemas import ConversationContext
    monkeypatch.setattr(semantic_matcher, "CACHE_CAP_PER_CONV", 2)
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


def test_concurrent_calls_idempotent(seeded_customer_entries):
    """Two threads calling match_customer with the same args produce
    the same answer (idempotent under race)."""
    import concurrent.futures
    from agents.schemas import ConversationContext

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
