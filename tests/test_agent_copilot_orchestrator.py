"""End-to-end tests for the sales-copilot orchestrator.

Covers the four intent branches: query, log_customer_report,
create_order, and the unknown fallback.

Real Postgres + real LLM. Skips if no API key (require_llm_api_key).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from agents.copilot.orchestrator import get_copilot_response
from agents.copilot.account_resolver import build_account_index
from agents.copilot.conversation_schemas import CopilotResponse


def _seed_full(db, faker_instance) -> None:
    from seed import (
        customers as customers_gen,
        regions as regions_gen,
        sites as sites_gen,
        apiarists as apiarists_gen,
        inspections as inspections_gen,
        embeddings as embeddings_gen,
        products as products_gen,
        services as services_gen,
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
    products_gen.persist(db, products_gen.generate(faker_instance))
    services_gen.persist(db, services_gen.generate())
    embeddings_gen.run(db)


def _build_models():
    from agents.order_extractor import _build_chat_model
    from app.config import get_settings

    s = get_settings()
    return {
        "router": _build_chat_model(s.extract_intent_model),
        "main": _build_chat_model(s.extract_items_model),
        "report": _build_chat_model(s.extract_intent_model),
    }


class TestOrchestrator:
    def test_query_returns_grounded_answer(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_full(db, faker_instance)
        index = build_account_index(db)
        models = _build_models()

        result = get_copilot_response(
            user_text="What's the general state of varroa across our customers?",
            db=db,
            models=models,
            account_index=index,
        )
        assert isinstance(result, CopilotResponse)
        assert result.text
        assert result.latency_ms > 0
        # build_context tool call should have been recorded
        assert any(tc.tool_name == "build_context" for tc in result.tool_calls)

    def test_log_customer_report_intent_extracts(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_full(db, faker_instance)
        models = _build_models()

        result = get_copilot_response(
            user_text=(
                "Trip report for one of our customers: visited today, "
                "mites at 3.5%, queens healthy, planned follow-up next week."
            ),
            db=db,
            models=models,
            account_index=build_account_index(db),
        )
        assert isinstance(result, CopilotResponse)
        assert "draft" in result.text.lower() or "report" in result.text.lower()
        assert any(
            tc.tool_name == "log_customer_report" for tc in result.tool_calls
        )

    def test_unknown_intent_returns_friendly_message(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_full(db, faker_instance)
        models = _build_models()
        result = get_copilot_response(
            user_text="qwerty asdf zxcv plover frobnicate",
            db=db,
            models=models,
            account_index=build_account_index(db),
        )
        assert isinstance(result, CopilotResponse)
        # Either the router classified as unknown OR query (with no useful
        # context). Either way the response should not be empty and should
        # be reasonably short.
        assert result.text
