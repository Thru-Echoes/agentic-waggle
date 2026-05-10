"""Tests for the copilot's context builder.

Builds a ContextBundle from a chat-turn query: customer summary (when
known), recent customer reports for that customer, and RAG chunks
retrieved from pgvector. Token-budget-aware (rejects overflowing
reports / chunks rather than blowing past).

E2E against real Postgres + real sentence-transformers.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

from sqlalchemy import text

from agents.copilot.context_builder import (
    ContextBundle,
    build_context,
    DEFAULT_TOKEN_BUDGET,
)
from agents.copilot.account_resolver import build_account_index


def _seed_full(db, faker_instance) -> None:
    """Seed enough for embeddings + customer reports to exist."""
    from seed import (
        customers as customers_gen,
        regions as regions_gen,
        sites as sites_gen,
        apiarists as apiarists_gen,
        inspections as inspections_gen,
        embeddings as embeddings_gen,
    )
    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    inspections = inspections_gen.generate(faker_instance, sites, apiarists)
    inspections_gen.persist(db, inspections)
    embeddings_gen.run(db)


class TestContextBundle:
    def test_known_customer_context_includes_basics(self, db, faker_instance) -> None:
        """Building context for a known customer returns their name + erp_no."""
        _seed_full(db, faker_instance)
        # Pick a real customer
        row = db.execute(
            text("SELECT id, name, erp_customer_no FROM customers LIMIT 1")
        ).mappings().one()
        customer_id = row["id"]
        if isinstance(customer_id, str):
            customer_id = uuid.UUID(customer_id)

        bundle = build_context(
            query="how are they doing?",
            customer_id=customer_id,
            db=db,
        )
        assert isinstance(bundle, ContextBundle)
        assert bundle.customer_name == row["name"]
        assert bundle.erp_customer_no == row["erp_customer_no"]

    def test_unknown_customer_returns_bundle_with_no_customer(
        self, db, faker_instance
    ) -> None:
        """A None customer_id yields a bundle with customer fields blank."""
        _seed_full(db, faker_instance)
        bundle = build_context(
            query="any general issues across customers?",
            customer_id=None,
            db=db,
        )
        assert bundle.customer_name is None
        assert bundle.erp_customer_no is None

    def test_rag_chunks_returned_for_topical_query(
        self, db, faker_instance
    ) -> None:
        """A topical query pulls chunks from pgvector — non-empty list."""
        _seed_full(db, faker_instance)
        bundle = build_context(
            query="varroa mite levels",
            customer_id=None,
            db=db,
            top_k=3,
        )
        # Hits from the seeded customer reports should surface
        assert len(bundle.rag_chunks) > 0
        assert all(isinstance(c, str) and c for c in bundle.rag_chunks)

    def test_token_budget_rejects_overflow(self, db, faker_instance) -> None:
        """A tiny budget caps the bundle's content size (estimated tokens)."""
        _seed_full(db, faker_instance)
        big_bundle = build_context(
            query="varroa", customer_id=None, db=db,
            token_budget=DEFAULT_TOKEN_BUDGET, top_k=10,
        )
        small_bundle = build_context(
            query="varroa", customer_id=None, db=db,
            token_budget=200, top_k=10,
        )
        # Tiny budget either trims chunks or shorter content
        assert small_bundle.estimated_tokens <= 200
        # Big budget allows more — strict ≥ given the same query
        assert big_bundle.estimated_tokens >= small_bundle.estimated_tokens
