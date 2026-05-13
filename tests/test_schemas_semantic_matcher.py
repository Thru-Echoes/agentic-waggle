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
    # Fix 4: pin match= to the confidence field so a different field's
    # ValidationError can't make this test pass silently.
    with pytest.raises(ValidationError, match="confidence"):
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


def test_sku_context_has_no_required_fields():
    """SkuContext is intentionally empty until Phase II — verify schema is genuinely empty."""
    assert SkuContext.model_fields == {}
    SkuContext()  # must not raise


# ---------------------------------------------------------------------------
# Fix 3: Missing test branches on _exactly_one_id validator
# ---------------------------------------------------------------------------


def test_sku_match_rejects_kind_none_with_id():
    """sku_kind=None requires both IDs null."""
    with pytest.raises(ValidationError, match="sku_kind None requires both IDs null"):
        SkuMatch(
            sku_kind=None,
            product_id=uuid.uuid4(),  # invalid — kind is None
            service_id=None,
            sku_code=None,
            name=None,
            confidence=0.0,
            evidence=[],
            alternatives=[],
        )


def test_sku_match_rejects_kind_set_with_neither_id():
    """sku_kind set without any ID — common typo."""
    with pytest.raises(ValidationError, match="exactly one"):
        SkuMatch(
            sku_kind="product",
            product_id=None,
            service_id=None,
            sku_code="X",
            name="X",
            confidence=0.9,
            evidence=[],
            alternatives=[],
        )


def test_sku_match_rejects_cross_kind_id():
    """sku_kind='product' with only service_id set must raise."""
    with pytest.raises(ValidationError, match="sku_kind='product' requires product_id"):
        SkuMatch(
            sku_kind="product",
            product_id=None,
            service_id=uuid.uuid4(),  # wrong kind
            sku_code="X",
            name="X",
            confidence=0.9,
            evidence=[],
            alternatives=[],
        )


def test_sku_match_service_kind_succeeds():
    """sku_kind='service' with service_id set is valid."""
    sid = uuid.uuid4()
    m = SkuMatch(
        sku_kind="service",
        product_id=None,
        service_id=sid,
        sku_code="SVC-INSP-VAR",
        name="Varroa inspection",
        confidence=0.9,
        evidence=["varroa check"],
        alternatives=[],
    )
    assert m.service_id == sid
    assert m.product_id is None


# ---------------------------------------------------------------------------
# Fix 2: max_length=3 on last_3_customer_ids
# ---------------------------------------------------------------------------


def test_conversation_context_rejects_too_many_recent_customers():
    """last_3_customer_ids enforces max length of 3."""
    with pytest.raises(ValidationError, match="at most 3 items"):
        ConversationContext(last_3_customer_ids=[uuid.uuid4() for _ in range(4)])
