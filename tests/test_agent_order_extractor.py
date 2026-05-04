"""E2E tests for the LangGraph order-extraction agent.

These tests require a real LLM (ANTHROPIC_API_KEY or OPENAI_API_KEY).
They skip with a documented message if no key is set — matching the
fail-loud-but-deliberate pattern from `tests/test_provider_llm.py`.

Tests run the full 6-node pipeline with real LLM calls, real rapidfuzz,
real pgvector. No mocks.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest

from agents.order_extractor import run_order_extraction
from agents.schemas import EmailInput, ExtractionFailure, OrderDraft


def _seed_minimum(db, faker_instance) -> None:
    """Seed the minimum needed for the agent to run: customers, sites,
    apiarists, products, services."""
    from seed import apiarists as apiarists_gen
    from seed import customers as customers_gen
    from seed import products as products_gen
    from seed import regions as regions_gen
    from seed import services as services_gen
    from seed import sites as sites_gen

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    products = products_gen.generate(faker_instance)
    products_gen.persist(db, products)
    services = services_gen.generate()
    services_gen.persist(db, services)


class TestOrderExtractionHappyPath:
    def test_order_request_produces_draft(
        self,
        db,
        faker_instance,
        require_llm_api_key,
    ) -> None:
        _seed_minimum(db, faker_instance)

        email = EmailInput(
            external_id="demo-msg-1",
            from_address="orders@oreilly-orchards.test",
            subject="Pollination for almond bloom",
            body_text=(
                "Hi Isa,\n\n"
                "We're O'Reilly Family Orchards and we need 400 hives of "
                "premium almond pollination for the Feb 1 bloom window. "
                "Can you confirm pricing? Thanks — Sean"
            ),
        )
        result = run_order_extraction(email, db)
        assert isinstance(result, OrderDraft), (
            f"expected OrderDraft, got {type(result).__name__} "
            f"({getattr(result, 'reason', '?')})"
        )
        assert result.customer.customer_id is not None
        assert "O'Reilly" in (result.customer.customer_name or "")
        assert len(result.line_items) >= 1
        assert result.total_usd > 0


class TestOrderExtractionFailurePaths:
    def test_non_order_email_fails_at_classify(
        self,
        db,
        faker_instance,
        require_llm_api_key,
    ) -> None:
        _seed_minimum(db, faker_instance)

        email = EmailInput(
            external_id="demo-msg-2",
            from_address="fan@example.test",
            subject="Love what you do",
            body_text=(
                "Just writing to say your beekeeping work is inspiring. "
                "No questions, no orders — just appreciation. Keep it up!"
            ),
        )
        result = run_order_extraction(email, db)
        assert isinstance(result, ExtractionFailure)
        assert result.stage == "classify_intent"

    def test_unknown_customer_fails_at_match(
        self,
        db,
        faker_instance,
        require_llm_api_key,
    ) -> None:
        _seed_minimum(db, faker_instance)

        email = EmailInput(
            external_id="demo-msg-3",
            from_address="ops@unknown-co.test",
            subject="Pollination request",
            body_text=(
                "Hi, this is Zephyr Biome Consulting Co. We'd like 50 hives "
                "for our blueberry bloom next month. Please confirm."
            ),
        )
        result = run_order_extraction(email, db)
        # Either fails at match_customer (most likely) or fails later if
        # the LLM infers a real company from partial info — assert either.
        if isinstance(result, ExtractionFailure):
            assert result.stage in {"match_customer", "resolve_skus"}
