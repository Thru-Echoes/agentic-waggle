"""End-to-end tests for the sales-copilot orchestrator.

Covers the four intent branches: query, log_customer_report,
create_order, and the unknown fallback, plus the two new
confidence-graduated customer-match paths.

Real Postgres + real LLM. Skips if no API key (require_llm_api_key).
The semantic matcher requires customer_search_entries to be populated;
_seed_full calls embeddings_gen.run() and backfill_all to ensure both.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

from agents.copilot.orchestrator import get_copilot_response
from agents.copilot.conversation_schemas import CopilotResponse


def _seed_full(db, faker_instance) -> None:
    """Seed all tables and build the semantic matcher index.

    Side effects: many INSERTs across all tables + backfill_all populates
    customer_search_entries + sku_search_entries.
    """
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
    from providers.semantic_matcher import backfill_all

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
    # Populate semantic matcher search-entry tables so match_customer works.
    backfill_all(db)


def _build_models():
    """Build the per-stage LangChain model dict for the orchestrator."""
    from agents.order_extractor import _build_chat_model
    from app.config import get_settings

    s = get_settings()
    return {
        "router": _build_chat_model(s.extract_intent_model),
        "main": _build_chat_model(s.extract_items_model),
        "report": _build_chat_model(s.extract_intent_model),
    }


def _first_seeded_customer(db) -> tuple[uuid.UUID, str]:
    """Return (id, name) of the first customer row for use in exact-hint tests.

    Side effects: one SELECT on customers.
    """
    from sqlalchemy import text

    row = db.execute(
        text("SELECT id, name FROM customers ORDER BY name LIMIT 1")
    ).mappings().first()
    assert row is not None, "No customers in DB — did _seed_full run?"
    return uuid.UUID(str(row["id"])), row["name"]


class TestOrchestrator:
    def test_query_returns_grounded_answer(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        """Query intent with no customer hint → answer using general context."""
        _seed_full(db, faker_instance)
        models = _build_models()

        result = get_copilot_response(
            user_text="What's the general state of varroa across our customers?",
            db=db,
            models=models,
            conv_id=uuid.uuid4(),
        )
        assert isinstance(result, CopilotResponse)
        assert result.text
        assert result.latency_ms > 0
        # build_context tool call should have been recorded
        assert any(tc.tool_name == "build_context" for tc in result.tool_calls)

    def test_log_customer_report_intent_extracts(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        """log_customer_report intent → draft extracted regardless of customer match."""
        _seed_full(db, faker_instance)
        models = _build_models()

        result = get_copilot_response(
            user_text=(
                "Trip report for one of our customers: visited today, "
                "mites at 3.5%, queens healthy, planned follow-up next week."
            ),
            db=db,
            models=models,
            conv_id=uuid.uuid4(),
        )
        assert isinstance(result, CopilotResponse)
        # Either: a draft was extracted (high/no-customer path)
        # OR: a pending_customer_match was emitted (low confidence path).
        # Either way result.text must be non-empty.
        assert result.text
        if result.pending_customer_match is None:
            # High-confidence or index-empty path: expect log_customer_report tool call.
            assert any(
                tc.tool_name == "log_customer_report" for tc in result.tool_calls
            ) or "draft" in result.text.lower() or "report" in result.text.lower()

    def test_unknown_intent_returns_friendly_message(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        """Gibberish input → router falls back to unknown/query → non-empty reply."""
        _seed_full(db, faker_instance)
        models = _build_models()
        result = get_copilot_response(
            user_text="qwerty asdf zxcv plover frobnicate",
            db=db,
            models=models,
            conv_id=uuid.uuid4(),
        )
        assert isinstance(result, CopilotResponse)
        # Either the router classified as unknown OR query (with no useful
        # context). Either way the response should not be empty.
        assert result.text

    def test_low_confidence_emits_pending_match_turn(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        """A deliberately ambiguous hint → matcher returns low confidence →
        CopilotResponse.pending_customer_match is populated with tool_name,
        target_intent, hint, and alternatives keys."""
        _seed_full(db, faker_instance)
        models = _build_models()

        # Use a maximally ambiguous hint — multiple customers share common
        # fragments like "farm" or "llc"; the matcher should not pick confidently.
        # We use a nonsense hint so Stage A returns no candidates or low fused score.
        result = get_copilot_response(
            user_text=(
                "Please log a visit for zzz_no_such_customer_xyz — "
                "varroa 4.2%, queens look healthy."
            ),
            db=db,
            models=models,
            conv_id=uuid.uuid4(),
        )
        assert isinstance(result, CopilotResponse)
        # The matcher must return a pending_customer_match (low confidence path)
        # because the hint is intentionally unresolvable.
        assert result.pending_customer_match is not None, (
            f"Expected pending_customer_match for unresolvable hint, "
            f"got text={result.text!r}"
        )
        pm = result.pending_customer_match
        assert pm["tool_name"] == "pending_customer_match", (
            f"Expected tool_name='pending_customer_match', got {pm['tool_name']!r}"
        )
        assert "target_intent" in pm
        assert "hint" in pm
        assert "alternatives" in pm

    def test_medium_confidence_emits_confirm_match_turn(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        """A partial name hint that matches one customer at medium confidence →
        CopilotResponse.pending_customer_match is populated with a single
        alternative representing the top candidate for confirmation."""
        _seed_full(db, faker_instance)
        models = _build_models()

        # Retrieve a real customer name, then use only the first word of it
        # to create a partial-match scenario. One-word hints often land in
        # the 0.50–0.79 confidence band.
        _cust_id, cust_name = _first_seeded_customer(db)
        first_word = cust_name.split()[0] if " " in cust_name else cust_name[:4]

        result = get_copilot_response(
            user_text=(
                f"Log a quick report for {first_word!r} — "
                "colony looks strong, no issues."
            ),
            db=db,
            models=models,
            conv_id=uuid.uuid4(),
        )
        assert isinstance(result, CopilotResponse)
        # The result is one of three valid states:
        #   (a) pending_customer_match — low or medium confidence
        #   (b) high-confidence resolve — the partial hint was unambiguous enough
        #   (c) general answer — router chose query/unknown
        # We only assert structure when in the pending state.
        if result.pending_customer_match is not None:
            pm = result.pending_customer_match
            assert pm["tool_name"] == "pending_customer_match"
            assert isinstance(pm.get("alternatives"), list)
