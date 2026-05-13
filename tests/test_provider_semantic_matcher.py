"""Tests for providers/semantic_matcher.py.

Layered: this file grows through Tasks 3-6. Stage A (hybrid retrieve) first,
then Stage B (LLM disambig), then the public API + cache.

Real deps: real pgvector, real sentence-transformers, real Postgres FTS.
LLM tests are added in later tasks and skip cleanly without an OPENAI_API_KEY.
"""
from __future__ import annotations

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
