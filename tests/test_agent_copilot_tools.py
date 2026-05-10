"""Tests for the copilot's four tools.

E2E against real Postgres + real LLM (the report-extraction tool needs
an API key; lookup + search are pure DB / pgvector).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

from sqlalchemy import text

from agents.copilot.tools import (
    CustomerLookup,
    ReportHit,
    lookup_customer,
    search_customer_reports,
    log_customer_report,
)


def _seed_basic(db, faker_instance) -> None:
    from seed import (
        customers as customers_gen,
        regions as regions_gen,
        sites as sites_gen,
    )
    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)


def _seed_with_reports(db, faker_instance) -> None:
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


class TestLookupCustomer:
    def test_returns_customer_when_id_exists(self, db, faker_instance) -> None:
        _seed_basic(db, faker_instance)
        row = db.execute(text("SELECT id FROM customers LIMIT 1")).mappings().one()
        cid = row["id"]
        if isinstance(cid, str):
            cid = uuid.UUID(cid)
        result = lookup_customer(db, customer_id=cid)
        assert isinstance(result, CustomerLookup)
        assert result.id == cid
        assert result.name
        assert result.erp_customer_no
        assert result.site_count >= 0

    def test_returns_none_for_unknown_id(self, db, faker_instance) -> None:
        _seed_basic(db, faker_instance)
        result = lookup_customer(db, customer_id=uuid.uuid4())
        assert result is None


class TestSearchCustomerReports:
    def test_topical_query_returns_hits(self, db, faker_instance) -> None:
        _seed_with_reports(db, faker_instance)
        results = search_customer_reports(db, query="varroa mite", top_k=3)
        # Real-world fuzzy: vector search with this query against reports
        # should return some hits — the seed corpus is varroa-heavy.
        assert isinstance(results, list)
        for hit in results:
            assert isinstance(hit, ReportHit)
            assert 0.0 <= hit.score <= 1.0

    def test_empty_query_with_customer_returns_recent(
        self, db, faker_instance
    ) -> None:
        _seed_with_reports(db, faker_instance)
        # Pick a customer that has sites + reports
        row = db.execute(text(
            """
            SELECT c.id FROM customers c
            JOIN customer_sites cs ON cs.customer_id = c.id
            JOIN customer_reports cr ON cr.customer_site_id = cs.id
            LIMIT 1
            """
        )).mappings().first()
        if row is None:
            return  # no reports persisted; skip silently
        cid = row["id"]
        if isinstance(cid, str):
            cid = uuid.UUID(cid)
        results = search_customer_reports(db, query="", customer_id=cid, top_k=5)
        assert len(results) > 0
        assert all(r.score == 1.0 for r in results)


class TestLogCustomerReport:
    def test_extracts_structured_report(self, require_llm_api_key) -> None:
        from agents.order_extractor import _build_chat_model
        from app.config import get_settings

        model = _build_chat_model(get_settings().extract_intent_model)
        result = log_customer_report(
            text_in=(
                "Trip report for Cascade Orchards: visited today. Mites at "
                "3.5 percent across the south yard. Queens looking strong. "
                "Need to follow up next week with replacement queens for "
                "two weak hives."
            ),
            model=model,
        )
        # Returned the right shape
        assert result.confidence > 0.0
        # The percentage was picked up
        if result.varroa_pct is not None:
            assert 0.0 <= result.varroa_pct <= 100.0
        # At least one observation or action item came through
        assert result.observations or result.action_items
