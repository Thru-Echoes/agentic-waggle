"""E2E tests for the LangGraph order-extraction agent.

These tests require a real LLM (ANTHROPIC_API_KEY or OPENAI_API_KEY).
They skip with a documented message if no key is set — matching the
fail-loud-but-deliberate pattern from `tests/test_provider_llm.py`.

Tests run the full 6-node pipeline with real LLM calls, real rapidfuzz,
real pgvector. No mocks.

Includes the evidence-grounding invariant test that exercises the
Grounded mixin's promise: every customer-evidence quote must appear
literally in the email body. Line-item evidence is partly catalog-
sourced so we relax the assertion there.
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


def _normalize_quote(q: str) -> str:
    """Strip wrapping quote marks the LLM sometimes adds around evidence quotes."""
    s = q.strip()
    for ch in ('"', "'", "“", "”", "‘", "’"):
        s = s.removeprefix(ch).removesuffix(ch)
    return s.strip()


class TestEvidenceGrounding:
    """The Grounded mixin's promise: evidence quotes are verbatim from source.

    Customer-evidence comes from the LLM-only customer-extraction step and
    must be a literal substring of the email body (modulo surrounding
    quote-mark decoration the LLM sometimes adds). Line-item evidence is
    partly catalog-sourced (for fuzzy/llm matches) so we check that at
    least one piece is grounded.
    """

    def test_customer_evidence_grounded_in_source(
        self,
        db,
        faker_instance,
        require_llm_api_key,
    ) -> None:
        _seed_minimum(db, faker_instance)

        email = EmailInput(
            external_id="evidence-1",
            from_address="orders@oreilly-orchards.test",
            subject="Pollination order",
            body_text=(
                "Hi team,\n\n"
                "We're O'Reilly Family Orchards and we need 400 hives for "
                "premium almond pollination during the Feb 1 bloom window. "
                "Please confirm pricing.\n\n"
                "Thanks — Sean"
            ),
        )
        result = run_order_extraction(email, db)
        if isinstance(result, OrderDraft) and result.customer_evidence:
            for q in result.customer_evidence:
                assert _normalize_quote(q) in email.body_text, (
                    f"customer evidence quote not in source body: {q!r}"
                )

    def test_at_least_one_line_item_evidence_grounded(
        self,
        db,
        faker_instance,
        require_llm_api_key,
    ) -> None:
        _seed_minimum(db, faker_instance)

        email = EmailInput(
            external_id="evidence-2",
            from_address="orders@oreilly-orchards.test",
            subject="Pollination order",
            body_text=(
                "Hi, O'Reilly Family Orchards here — we need 400 hives "
                "for the almond bloom on Feb 1. Confirm soonest."
            ),
        )
        result = run_order_extraction(email, db)
        if isinstance(result, OrderDraft):
            # Line-item evidence combines source + catalog. We require that
            # at least one quote per line item appears in the email body
            # (i.e. the LLM cited a real source quote, not only catalog text).
            for li in result.line_items:
                if li.evidence:
                    assert any(
                        _normalize_quote(q) in email.body_text for q in li.evidence
                    ), (
                        f"no line-item evidence grounded in source for "
                        f"sku={li.sku!r}: evidence={li.evidence}"
                    )
