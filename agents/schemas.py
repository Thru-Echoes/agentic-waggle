"""Pydantic I/O models for the LangGraph order-extraction agent.

These are the types crossing agent ⇄ caller boundaries. The LangChain
chat model uses `with_structured_output(Model)` on several of them to get
validated Pydantic responses instead of raw JSON.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from db.enums import OrderState


class EmailInput(BaseModel):
    """Shape the extractor expects for an inbound email.

    `external_id` is preserved on `OrderDraft.source_email_external_id`
    so downstream persistence can look up the `synced_emails` row.
    """

    model_config = ConfigDict(frozen=True)

    external_id: str | None = Field(
        default=None,
        description="Gmail-style message id. None for ad-hoc manual drafts.",
    )
    from_address: str = Field(..., min_length=3)
    subject: str = Field(..., max_length=998)
    body_text: str = Field(..., min_length=1, max_length=64_000)


class IntentClassification(BaseModel):
    """First-node output: is this email asking for an order?"""

    model_config = ConfigDict(frozen=True)

    intent: Literal["order_request", "inquiry", "complaint", "other"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., max_length=500)


class CustomerReference(BaseModel):
    """The customer string the email references (pre-resolution)."""

    model_config = ConfigDict(frozen=True)

    mentioned_name: str = Field(
        ...,
        min_length=1,
        description="Raw customer name as it appears in the email.",
    )
    requester_is_customer: bool = Field(
        default=True,
        description=(
            "True if the from_address likely belongs to the customer "
            "themselves; False for third-party messages."
        ),
    )


class CustomerMatch(BaseModel):
    """Result of matching `CustomerReference.mentioned_name` against the DB."""

    model_config = ConfigDict(frozen=True)

    customer_id: uuid.UUID | None = None
    customer_name: str | None = None
    erp_customer_no: str | None = None
    score: float = Field(
        ..., ge=0.0, le=100.0, description="rapidfuzz 0-100 similarity."
    )
    ambiguous: bool = Field(
        default=False,
        description="True if 2+ candidates tied above the ambiguity threshold.",
    )
    candidates_considered: list[str] = Field(
        default_factory=list,
        description="Top-5 candidate names inspected during matching.",
    )


class RequestedLineItem(BaseModel):
    """One line item extracted from the email body (SKU unresolved)."""

    model_config = ConfigDict(frozen=True)

    description: str = Field(..., min_length=1, max_length=300)
    quantity: int = Field(..., ge=1)
    mentioned_sku: str | None = Field(
        default=None,
        description="SKU if the email cited one literally. Otherwise None.",
    )
    mentioned_unit_price_usd: Decimal | None = None


class ResolvedLineItem(BaseModel):
    """A RequestedLineItem after SKU lookup + pricing."""

    model_config = ConfigDict(frozen=True)

    sku: str = Field(...)
    product_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    quantity: int = Field(..., ge=1)
    unit_price_usd: Decimal
    match_method: Literal["exact_sku", "fuzzy_name", "llm_guess"]
    source_description: str = Field(
        ..., description="The RequestedLineItem.description that produced this."
    )


class OrderDraft(BaseModel):
    """Final output of the extraction pipeline.

    Mirrors the fields needed to INSERT a row into `orders` + its
    `order_line_items`. State is fixed to EXTRACTED — downstream workflow
    promotes it to UNDER_REVIEW / APPROVED etc.
    """

    model_config = ConfigDict(frozen=True)

    state: OrderState = Field(default=OrderState.EXTRACTED, frozen=True)
    customer: CustomerMatch
    line_items: list[ResolvedLineItem] = Field(..., min_length=1)
    total_usd: Decimal = Field(..., gt=Decimal("0"))
    pollination_date: date | None = None
    source_email_external_id: str | None = None
    extraction_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Notes the agent recorded during extraction — ambiguous matches, "
            "price guesses, etc. Surfaced in the /orders review UI."
        ),
    )


class ExtractionFailure(BaseModel):
    """Returned when the pipeline cannot produce a valid OrderDraft.

    Fail-loud: routes that get this should surface it to the user rather
    than persisting a half-order. `stage` identifies which node blocked.
    """

    model_config = ConfigDict(frozen=True)

    stage: Literal[
        "classify_intent",
        "extract_customer",
        "match_customer",
        "extract_line_items",
        "resolve_skus",
        "compute_total",
    ]
    reason: str = Field(..., max_length=1000)
    partial_state: dict = Field(
        default_factory=dict,
        description="Snapshot of the graph state at failure time.",
    )


ExtractionResult = OrderDraft | ExtractionFailure
