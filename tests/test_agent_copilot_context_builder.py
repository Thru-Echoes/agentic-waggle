"""Tests for the copilot's context builder.

Builds a ContextBundle from a chat-turn query: customer summary (when
known), recent customer reports for that customer, and RAG chunks
retrieved from pgvector. Token-budget-aware (rejects overflowing
reports / chunks rather than blowing past).

E2E against real Postgres + real sentence-transformers.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest

import uuid

from sqlalchemy import text

from agents.copilot.context_builder import (
    ContextBundle,
    build_context,
    DEFAULT_TOKEN_BUDGET,
)
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


class TestReportIdsAndMultiSite:
    def test_recent_reports_carry_real_report_ids(self, db, faker_instance) -> None:
        _seed_full(db, faker_instance)
        row = db.execute(text(
            "SELECT c.id FROM customers c JOIN customer_sites s ON s.customer_id=c.id "
            "JOIN customer_reports r ON r.customer_site_id=s.id LIMIT 1"
        )).mappings().one()
        cid = row["id"]
        if isinstance(cid, str):
            cid = uuid.UUID(cid)
        bundle = build_context(query="how are they doing?", customer_id=cid, db=db)
        assert bundle.recent_reports, "customer with reports must get report summaries"
        for rep in bundle.recent_reports:
            assert isinstance(rep.report_id, uuid.UUID)
            assert db.execute(text("SELECT 1 FROM customer_reports WHERE id=:id"), {"id": str(rep.report_id)}).first()

    def test_rag_chunks_come_from_any_of_the_customers_sites(self, db, faker_instance) -> None:
        """Pick the customer with chunks at the most sites and check the filter
        does not collapse to a single site."""
        _seed_full(db, faker_instance)
        row = db.execute(text(
            "SELECT s.customer_id, COUNT(DISTINCT ch.metadata->>'customer_site_id') AS n "
            "FROM chunks ch JOIN customer_sites s ON s.id::text = ch.metadata->>'customer_site_id' "
            "GROUP BY s.customer_id ORDER BY n DESC LIMIT 1"
        )).mappings().one()
        cid = row["customer_id"]
        if isinstance(cid, str):
            cid = uuid.UUID(cid)
        if int(row["n"]) < 2:
            pytest.skip("seed produced no customer with chunks at two or more sites")
        from agents.copilot.context_builder import _rag_chunks

        chunks = _rag_chunks("varroa mites brood inspection", db, top_k=50, customer_id=cid)
        site_rows = db.execute(text(
            "SELECT DISTINCT metadata->>'customer_site_id' FROM chunks WHERE content = ANY(:c)"
        ), {"c": chunks}).scalars().all()
        assert len(site_rows) >= 2, "chunks must span more than one of the customer's sites"
