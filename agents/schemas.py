"""Pydantic I/O models for the LangGraph order-extraction agent.

These types cross agent ⇄ caller boundaries. The OpenAI chat model fills
several of them via native structured outputs (`response_format`).

`Grounded` is the mixin that enforces verbatim-quote evidence + a
self-rated confidence on every LLM-filled schema. It exists because
LLM extractions without grounding have no defense against hallucination;
forcing the model to cite quotes from the source it just read makes
hallucinations structurally visible — the runtime audits that each
quoted string appears literally in the source, and rejects extractions
that cite text the source doesn't contain.

`confidence` is backend-only. Never rendered in user-facing templates.
It feeds monitoring, low-confidence flagging, and the model-comparison
benchmark.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from db.enums import OrderState


# ---------------------------------------------------------------------------
# Grounded mixin — evidence + confidence on every LLM-filled schema
# ---------------------------------------------------------------------------


class Grounded(BaseModel):
    """Mixin for any schema the LLM is asked to fill.

    Subclasses inherit two required fields:

    - ``evidence: list[str]`` — verbatim quotes from the source text that
      justify the populated fields. The runtime audits that each quote
      appears literally in the source string. Quotes that don't are
      flagged and the extraction is rejected.
    - ``confidence: float`` — the LLM's self-rated confidence in the
      extraction (0.0–1.0). Backend-only.

    Empty evidence is allowed structurally (some deterministic resolver
    paths don't have a quote to cite), but the prompts make clear that
    LLM-filled instances should populate it.
    """

    model_config = ConfigDict(frozen=True)

    evidence: list[str] = Field(
        ...,
        description=(
            "Verbatim quotes from the source text that justify the "
            "populated fields. Audited at runtime — quotes that don't "
            "appear literally in the source mark the extraction as "
            "hallucinated."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Self-rated confidence in this extraction (0.0-1.0). "
            "Backend-only — never rendered in user templates."
        ),
    )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class EmailInput(BaseModel):
    """Shape the extractor expects for an inbound email.

    ``external_id`` is preserved on ``OrderDraft.source_email_external_id``
    so downstream persistence can look up the ``synced_emails`` row.
    """

    model_config = ConfigDict(frozen=True)

    external_id: str | None = Field(
        default=None,
        description="Gmail-style message id. None for ad-hoc manual drafts.",
    )
    from_address: str = Field(..., min_length=3)
    subject: str = Field(..., max_length=998)
    body_text: str = Field(..., min_length=1, max_length=64_000)


# ---------------------------------------------------------------------------
# Per-node LLM-filled schemas (all Grounded)
# ---------------------------------------------------------------------------


class IntentClassification(Grounded):
    """First-node output: is this email asking for an order?"""

    intent: Literal["order_request", "inquiry", "complaint", "other"]
    reasoning: str = Field(..., max_length=500)


class CustomerReference(Grounded):
    """The customer string the email references (pre-resolution)."""

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


class RequestedLineItem(Grounded):
    """One line item extracted from the email body (SKU unresolved)."""

    description: str = Field(..., min_length=1, max_length=300)
    quantity: int = Field(..., ge=1)
    mentioned_sku: str | None = Field(
        default=None,
        description="SKU if the email cited one literally. Otherwise None.",
    )
    mentioned_unit_price_usd: Decimal | None = None


# ---------------------------------------------------------------------------
# Semantic matcher schemas — Grounded results with evidence + confidence
# ---------------------------------------------------------------------------


class ConversationContext(BaseModel):
    """Per-turn context passed to match_customer.

    Lets the LLM disambiguator resolve pronouns ("they"), prefer the
    active account on ambiguous ties, and weigh recent customers higher.

    No side effects — pure data container.
    """

    model_config = ConfigDict(frozen=True)

    active_account_id: uuid.UUID | None = Field(
        default=None,
        description="Customer currently active in the conversation, if any.",
    )
    last_3_customer_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Recent customer IDs referenced in this conversation.",
    )


class SkuContext(BaseModel):
    """Placeholder context for SKU matching (Phase II will populate).

    No side effects — pure data container.
    """

    model_config = ConfigDict(frozen=True)


class CustomerCandidate(BaseModel):
    """One alternative customer surfaced by the matcher.

    No side effects — pure data container.
    """

    model_config = ConfigDict(frozen=True)

    customer_id: uuid.UUID
    customer_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: Literal["name", "alias", "site"] = Field(
        ..., description="Which kind of entry matched (name/alias/site)."
    )


class CustomerMatch(Grounded):
    """Result of resolving a free-text customer reference to a row.

    Grounded — evidence quotes the user's text that justified the match.
    customer_id is None iff the matcher could not pick confidently
    (caller decides whether to ask the user or to fall through).

    Inherits ``evidence`` and ``confidence`` from Grounded.
    No side effects — pure data container.
    """

    model_config = ConfigDict(frozen=True)

    customer_id: uuid.UUID | None = None
    customer_name: str | None = None
    alternatives: list[CustomerCandidate] = Field(default_factory=list)


class SkuCandidate(BaseModel):
    """One alternative SKU surfaced by the matcher.

    No side effects — pure data container.
    """

    model_config = ConfigDict(frozen=True)

    sku_kind: Literal["product", "service"]
    product_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    sku_code: str
    name: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class SkuMatch(Grounded):
    """Result of resolving a free-text product/service reference.

    Exactly one of product_id / service_id is non-null when sku_kind is
    not None. When sku_kind is None the matcher could not resolve.

    Inherits ``evidence`` and ``confidence`` from Grounded.
    No side effects — pure data container.
    """

    model_config = ConfigDict(frozen=True)

    sku_kind: Literal["product", "service"] | None = None
    product_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    sku_code: str | None = None
    name: str | None = None
    alternatives: list[SkuCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_id(self) -> "SkuMatch":
        """Enforce exactly one of product_id/service_id when sku_kind is set."""
        if self.sku_kind is None:
            if self.product_id is not None or self.service_id is not None:
                raise ValueError("sku_kind None requires both IDs null")
            return self
        # sku_kind is "product" or "service" — exactly one ID must be set
        if (self.product_id is None) == (self.service_id is None):
            raise ValueError(
                "SkuMatch must set exactly one of product_id/service_id "
                "when sku_kind is not None"
            )
        return self


# ---------------------------------------------------------------------------
# Resolved item — Grounded so the LLM-fallback path can cite catalog evidence
# ---------------------------------------------------------------------------


class ResolvedLineItem(Grounded):
    """A RequestedLineItem after SKU lookup + pricing.

    Inherits Grounded because the LLM-fallback resolver (called when
    deterministic fuzzy match misses) produces evidence + confidence.
    Deterministic resolutions (``exact_sku``, ``fuzzy_name``) populate
    evidence as ``[]`` and confidence near 1.0.
    """

    sku: str = Field(...)
    product_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    quantity: int = Field(..., ge=1)
    unit_price_usd: Decimal
    match_method: Literal["exact_sku", "fuzzy_name", "llm_guess"]
    source_description: str = Field(
        ..., description="The RequestedLineItem.description that produced this."
    )


# ---------------------------------------------------------------------------
# OrderDraft — final output, aggregates per-step confidences
# ---------------------------------------------------------------------------


class OrderDraft(BaseModel):
    """Final output of the extraction pipeline.

    Mirrors the fields needed to INSERT a row into ``orders`` plus its
    ``order_line_items``. State is fixed to EXTRACTED — downstream
    workflow promotes it through UNDER_REVIEW / APPROVED / etc.

    ``overall_confidence`` is aggregated from per-step confidences using
    ``min()`` — any weak step drags the overall down. Backend-only.
    """

    model_config = ConfigDict(frozen=True)

    state: OrderState = Field(default=OrderState.EXTRACTED, frozen=True)
    customer: CustomerMatch
    line_items: list[ResolvedLineItem] = Field(..., min_length=1)
    total_usd: Decimal = Field(..., gt=Decimal("0"))
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Aggregated confidence across intent + customer + each line "
            "item. Backend-only — never rendered."
        ),
    )
    extraction_concerns: list[str] = Field(
        default_factory=list,
        description=(
            "Plain-language notes about ambiguities, low-confidence steps, "
            "or judgment calls the agent made. Surfaced in the order review "
            "UI as items the salesperson should double-check."
        ),
    )
    customer_evidence: list[str] = Field(
        default_factory=list,
        description=(
            "Verbatim quotes from the source email that named the customer "
            "(sourced from the LLM customer-extraction step). Renders as a "
            "'why this customer' tooltip in the order review UI."
        ),
    )
    pollination_date: date | None = None
    source_email_external_id: str | None = None


# ---------------------------------------------------------------------------
# Failure type
# ---------------------------------------------------------------------------


class ExtractionFailure(BaseModel):
    """Returned when the pipeline cannot produce a valid OrderDraft.

    Distinguished from success at the type level — callers must branch on
    ``isinstance(result, OrderDraft)`` vs ``isinstance(result, ExtractionFailure)``.

    The ``not_order_intent`` field is populated when ``stage='classify_intent'``
    AND the email was classified as a valid non-order intent (inquiry,
    complaint, other). The UI uses this to render an informational card
    rather than an error card — receiving a question email is not a
    failure of the system.
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
    not_order_intent: Literal["inquiry", "complaint", "other"] | None = Field(
        default=None,
        description=(
            "Set when stage='classify_intent' and the email was a valid "
            "non-order intent. Drives the UI's informational-vs-error split."
        ),
    )
    partial_state: dict = Field(
        default_factory=dict,
        description="Snapshot of the graph state at failure time.",
    )


ExtractionResult = OrderDraft | ExtractionFailure
