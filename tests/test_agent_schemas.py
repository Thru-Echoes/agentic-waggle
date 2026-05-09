"""Schema-level tests for agents/schemas.py — Grounded mixin + retrofits.

Every schema the LLM fills directly must inherit `Grounded` and therefore
must accept `evidence: list[str]` (verbatim quotes) and
`confidence: float` (0.0–1.0). Confidence is backend-only and never
rendered in user-facing templates.

These tests are pure Pydantic validation — no DB, no LLM. They run under
`make test-unit`.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.schemas import (
    CustomerMatch,
    CustomerReference,
    Grounded,
    IntentClassification,
    OrderDraft,
    RequestedLineItem,
    ResolvedLineItem,
)


# ---------------------------------------------------------------------------
# Grounded mixin contract
# ---------------------------------------------------------------------------


def test_grounded_is_a_pydantic_base():
    """Grounded is importable as a Pydantic BaseModel-derived mixin."""
    # Smoke: it has the two required fields declared.
    assert "evidence" in Grounded.model_fields
    assert "confidence" in Grounded.model_fields


def test_grounded_subclass_requires_confidence():
    """A Grounded subclass cannot be instantiated without confidence."""
    with pytest.raises(ValidationError):
        IntentClassification(  # type: ignore[call-arg]
            intent="order_request",
            reasoning="r",
            evidence=["q"],
        )


def test_grounded_confidence_must_be_in_unit_interval():
    """confidence outside [0.0, 1.0] is rejected."""
    with pytest.raises(ValidationError):
        IntentClassification(
            intent="order_request",
            reasoning="r",
            evidence=["q"],
            confidence=1.5,
        )
    with pytest.raises(ValidationError):
        IntentClassification(
            intent="order_request",
            reasoning="r",
            evidence=["q"],
            confidence=-0.1,
        )


def test_grounded_evidence_must_be_list_of_strings():
    """evidence rejects non-string elements."""
    with pytest.raises(ValidationError):
        IntentClassification(
            intent="order_request",
            reasoning="r",
            evidence=[42, "valid"],  # type: ignore[list-item]
            confidence=0.9,
        )


def test_grounded_evidence_may_be_empty():
    """Empty evidence list is allowed (e.g., deterministic resolver path)."""
    obj = IntentClassification(
        intent="order_request",
        reasoning="r",
        evidence=[],
        confidence=0.9,
    )
    assert obj.evidence == []


# ---------------------------------------------------------------------------
# Each LLM-filled schema is Grounded
# ---------------------------------------------------------------------------


def test_intent_classification_is_grounded():
    """IntentClassification accepts evidence + confidence."""
    obj = IntentClassification(
        intent="order_request",
        reasoning="The email asks to purchase 40 hives.",
        evidence=["we'd like to order 40 hives"],
        confidence=0.95,
    )
    assert obj.intent == "order_request"
    assert obj.evidence == ["we'd like to order 40 hives"]
    assert obj.confidence == 0.95


def test_customer_reference_is_grounded():
    """CustomerReference accepts evidence + confidence."""
    obj = CustomerReference(
        mentioned_name="St. Agnes Pollination",
        requester_is_customer=True,
        evidence=["St. Agnes Pollination"],
        confidence=0.92,
    )
    assert obj.mentioned_name == "St. Agnes Pollination"
    assert obj.confidence == 0.92


def test_requested_line_item_is_grounded():
    """RequestedLineItem accepts evidence + confidence."""
    obj = RequestedLineItem(
        description="hives",
        quantity=40,
        mentioned_sku=None,
        mentioned_unit_price_usd=None,
        evidence=["order 40 hives"],
        confidence=0.88,
    )
    assert obj.quantity == 40
    assert obj.confidence == 0.88


def test_resolved_line_item_is_grounded_with_llm_fallback():
    """ResolvedLineItem populates evidence + confidence, useful for LLM-fallback path."""
    obj = ResolvedLineItem(
        sku="HIVE-LANG-8F-FULL",
        product_id=uuid.uuid4(),
        quantity=40,
        unit_price_usd=Decimal("420.00"),
        match_method="llm_guess",
        source_description="hives",
        evidence=["Langstroth 8-Frame Full Hive"],
        confidence=0.78,
    )
    assert obj.match_method == "llm_guess"
    assert obj.confidence == 0.78


def test_resolved_line_item_grounded_evidence_can_be_empty_for_exact_match():
    """Exact-SKU matches don't need a verbatim quote — evidence may be empty,
    but confidence is still required (set high by the resolver)."""
    obj = ResolvedLineItem(
        sku="HIVE-LANG-8F-FULL",
        product_id=uuid.uuid4(),
        quantity=40,
        unit_price_usd=Decimal("420.00"),
        match_method="exact_sku",
        source_description="HIVE-LANG-8F-FULL",
        evidence=[],
        confidence=1.0,
    )
    assert obj.confidence == 1.0


# ---------------------------------------------------------------------------
# CustomerMatch is deterministic but carries confidence for aggregation
# ---------------------------------------------------------------------------


def test_customer_match_carries_confidence_for_aggregation():
    """CustomerMatch is deterministic (not Grounded — no evidence field),
    but exposes confidence in [0,1] derived from the fuzzy score band so
    downstream OrderDraft.overall_confidence can fold it in."""
    high = CustomerMatch(
        customer_id=uuid.uuid4(),
        customer_name="St. Agnes Pollination",
        erp_customer_no="C-01004",
        score=95.0,
        confidence=0.95,
    )
    assert high.confidence == 0.95


def test_customer_match_confidence_bounded():
    """CustomerMatch.confidence is bounded [0,1]."""
    with pytest.raises(ValidationError):
        CustomerMatch(score=95.0, confidence=1.5)


def test_customer_match_has_no_evidence_field():
    """CustomerMatch is deterministic — it does NOT inherit Grounded
    (no verbatim quotes available from a fuzzy-match score)."""
    assert "evidence" not in CustomerMatch.model_fields


# ---------------------------------------------------------------------------
# OrderDraft aggregates per-step confidence into overall_confidence
# ---------------------------------------------------------------------------


def _minimal_resolved_item() -> ResolvedLineItem:
    return ResolvedLineItem(
        sku="HIVE-LANG-8F-FULL",
        product_id=uuid.uuid4(),
        quantity=1,
        unit_price_usd=Decimal("420.00"),
        match_method="exact_sku",
        source_description="hive",
        evidence=[],
        confidence=1.0,
    )


def _minimal_customer_match() -> CustomerMatch:
    return CustomerMatch(
        customer_id=uuid.uuid4(),
        customer_name="Test Co.",
        erp_customer_no="C-00001",
        score=100.0,
        confidence=1.0,
    )


def test_order_draft_requires_overall_confidence():
    """OrderDraft requires overall_confidence (aggregated from per-step)."""
    with pytest.raises(ValidationError):
        OrderDraft(  # type: ignore[call-arg]
            customer=_minimal_customer_match(),
            line_items=[_minimal_resolved_item()],
            total_usd=Decimal("420.00"),
        )


def test_order_draft_overall_confidence_bounded():
    """OrderDraft.overall_confidence must be in [0,1]."""
    with pytest.raises(ValidationError):
        OrderDraft(
            customer=_minimal_customer_match(),
            line_items=[_minimal_resolved_item()],
            total_usd=Decimal("420.00"),
            overall_confidence=1.5,
        )


def test_order_draft_accepts_valid_overall_confidence():
    """Happy path."""
    draft = OrderDraft(
        customer=_minimal_customer_match(),
        line_items=[_minimal_resolved_item()],
        total_usd=Decimal("420.00"),
        overall_confidence=0.9,
    )
    assert draft.overall_confidence == 0.9


def test_order_draft_extraction_concerns_default_empty_list():
    """OrderDraft has extraction_concerns: list[str] defaulting to empty."""
    draft = OrderDraft(
        customer=_minimal_customer_match(),
        line_items=[_minimal_resolved_item()],
        total_usd=Decimal("420.00"),
        overall_confidence=0.9,
    )
    assert draft.extraction_concerns == []
