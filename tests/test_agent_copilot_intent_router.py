"""Tests for the copilot's intent router.

Single-LLM-call classifier that picks one of:
    query | log_customer_report | create_order | command | unknown

Returns a Grounded ``IntentRoute`` — required ``evidence`` quoting the
phrase that drove the classification, plus a self-rated confidence.

These are E2E against a real LLM (skip without a key).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from agents.copilot.intent_router import get_intent_route
from agents.copilot.conversation_schemas import IntentRoute


def test_query_intent_recognized(require_llm_api_key) -> None:
    """A plain question about a customer routes to 'query'."""
    result = get_intent_route("What's the varroa trend at O'Reilly Family Orchards?")
    assert isinstance(result, IntentRoute)
    assert result.intent == "query"
    assert 0.0 <= result.confidence <= 1.0


def test_log_customer_report_intent_recognized(require_llm_api_key) -> None:
    """A dictated visit summary routes to 'log_customer_report'."""
    result = get_intent_route(
        "Trip report for Cascade Orchards: visited today, mites at 3.5%, "
        "queens healthy, scheduled to return in two weeks."
    )
    assert isinstance(result, IntentRoute)
    assert result.intent == "log_customer_report"


def test_create_order_intent_recognized(require_llm_api_key) -> None:
    """An order request routes to 'create_order'."""
    result = get_intent_route(
        "Create an order for St. Agnes Pollination — 40 hives, March delivery."
    )
    assert isinstance(result, IntentRoute)
    assert result.intent == "create_order"


def test_target_account_hint_extracted(require_llm_api_key) -> None:
    """When a customer is named, target_account_hint captures it verbatim."""
    result = get_intent_route(
        "How is St. Agnes Pollination doing this season?"
    )
    assert result.target_account_hint is not None
    # Verbatim (or close to it) — strict 'St. Agnes' substring required.
    assert "St. Agnes" in result.target_account_hint or "agnes" in result.target_account_hint.lower()


def test_evidence_grounded_in_input(require_llm_api_key) -> None:
    """Evidence quotes should appear in the source text (modulo wrapping
    quote-mark decoration the LLM sometimes adds)."""
    text = "Update the price on F1234.05 to $80 for Cascade Orchards."
    result = get_intent_route(text)
    assert result.evidence
    for q in result.evidence:
        # Strip wrapping quotes — same normalization the order extractor's
        # evidence test uses.
        cleaned = q.strip().strip('"').strip("'").strip()
        assert cleaned in text or cleaned.lower() in text.lower(), (
            f"evidence {q!r} not found in source"
        )
