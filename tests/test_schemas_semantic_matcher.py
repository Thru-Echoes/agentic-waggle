"""New matcher schemas: Grounded shape, alternatives, exactly-one for SKU."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from agents.schemas import (
    ConversationContext,
    CustomerCandidate,
    CustomerMatch,
    SkuCandidate,
    SkuContext,
    SkuMatch,
)


def test_customer_match_grounded_shape():
    cid = uuid.uuid4()
    m = CustomerMatch(
        customer_id=cid,
        customer_name="O'Reilly Family Orchards",
        confidence=0.91,
        evidence=["O'Reilly", "back-40"],
        alternatives=[
            CustomerCandidate(
                customer_id=uuid.uuid4(),
                customer_name="O'Connell Brothers",
                confidence=0.62,
                source="name",
            )
        ],
    )
    assert m.customer_id == cid
    assert m.confidence == 0.91
    assert len(m.evidence) == 2
    assert len(m.alternatives) == 1


def test_customer_match_null_for_no_resolution():
    m = CustomerMatch(
        customer_id=None,
        customer_name=None,
        confidence=0.3,
        evidence=["the company"],
        alternatives=[],
    )
    assert m.customer_id is None
    assert m.customer_name is None


def test_customer_match_confidence_range():
    with pytest.raises(ValidationError):
        CustomerMatch(
            customer_id=None,
            customer_name=None,
            confidence=1.5,  # out of range
            evidence=[],
            alternatives=[],
        )


def test_sku_match_exactly_one_id():
    pid = uuid.uuid4()
    m = SkuMatch(
        sku_kind="product",
        product_id=pid,
        service_id=None,
        sku_code="PKG-BEES-3LB-ITA",
        name="Italian queens (3-lb package)",
        confidence=0.95,
        evidence=["Italian queens"],
        alternatives=[],
    )
    assert m.product_id == pid
    assert m.service_id is None


def test_sku_match_rejects_both_ids():
    with pytest.raises(ValidationError, match="exactly one"):
        SkuMatch(
            sku_kind="product",
            product_id=uuid.uuid4(),
            service_id=uuid.uuid4(),  # both set — invalid
            sku_code="X",
            name="X",
            confidence=0.9,
            evidence=[],
            alternatives=[],
        )


def test_sku_match_null_for_no_resolution():
    m = SkuMatch(
        sku_kind=None,
        product_id=None,
        service_id=None,
        sku_code=None,
        name=None,
        confidence=0.0,
        evidence=[],
        alternatives=[],
    )
    assert m.sku_kind is None


def test_conversation_context_defaults():
    ctx = ConversationContext()
    assert ctx.active_account_id is None
    assert ctx.last_3_customer_ids == []


def test_sku_context_is_empty_for_phase_i():
    """SkuContext is a placeholder until customer-aware SKU bias arrives."""
    ctx = SkuContext()
    # No required fields; instantiation alone is the assertion.
    assert ctx is not None
