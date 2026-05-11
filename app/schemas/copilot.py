"""Request / response Pydantic models for the sales copilot routes.

These are the boundary shapes for the HTTP layer; they translate to / from
``db.models.Conversation`` + ``db.models.ChatTurn`` inside the route
handlers. Distinct from ``agents/copilot/conversation_schemas.py`` — those
are the orchestrator's internal types; these are what the client sends and
receives.

Convention follows ``app/schemas/__init__.py``:
    * ``<Resource>Out``  — response model (read path)
    * ``<Resource>In``   — request body model (write path)
    * ``<Resource>ListOut`` — paginated list response

All models use ``ConfigDict(frozen=True)`` — request bodies and read shapes
are immutable once constructed.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ChatTurnIn(BaseModel):
    """User-facing input for a new chat turn.

    Submitted as a form post by the HTMX composer. The route handler builds
    a full ChatTurn DB row from this plus the orchestrator's response.
    ``conversation_id`` is required; ``GET /copilot`` auto-creates a
    conversation if none exists yet.
    """

    model_config = ConfigDict(frozen=True)

    conversation_id: uuid.UUID = Field(
        ...,
        description="Conversation this turn belongs to.",
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=8_000,
        description="The user's message — required for every chat turn.",
    )


class ConversationCreateIn(BaseModel):
    """Body for ``POST /copilot/conversations``.

    Both fields are optional: the rail's "+ New conversation" button posts
    an empty body and the route assigns defaults. Tests can pin a starting
    account by passing ``active_account_id``.
    """

    model_config = ConfigDict(frozen=True)

    active_account_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Customer to anchor this conversation on. May be omitted; the "
            "orchestrator will resolve from each turn's intent route."
        ),
    )


class DraftConfirmIn(BaseModel):
    """Empty body for the confirm endpoints — POSTed by HTMX form submit.

    The draft itself is stashed in the assistant turn's ``tool_calls``
    JSONB; the confirm endpoint reads it from there and persists. We keep
    this body shape explicit for OpenAPI / documentation clarity even
    though it carries no fields.
    """

    model_config = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------
# Read shapes (returned by JSON API + used by Jinja templates)
# ---------------------------------------------------------------------------


ChatTurnRole = Literal["user", "assistant", "system", "tool"]


class ToolCallOut(BaseModel):
    """Read-side shape for one tool call recorded under a chat turn.

    Mirrors ``agents.copilot.conversation_schemas.ToolCallRecord`` but on
    the API boundary. The synthetic ``pending_draft`` records produced by
    the route handler are NOT surfaced as ``tool_calls`` here — the route
    splits them off into ``pending_order_draft`` / ``pending_report_draft``
    on the parent ``ChatTurnOut``.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_excerpt: str = Field(..., max_length=4096)
    latency_ms: int = Field(..., ge=0)
    tokens: int = Field(default=0, ge=0)


class CitationOut(BaseModel):
    """Read-side citation chip — clickable, links to the source row."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["customer_report", "order", "email", "customer", "product"]
    id: uuid.UUID
    label: str
    excerpt: str = Field(..., max_length=400)


class ChatTurnOut(BaseModel):
    """One persisted chat turn rendered back to the client."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: ChatTurnRole
    text: str
    tool_calls: list[ToolCallOut] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    citations: list[CitationOut] = Field(default_factory=list)
    model: str | None = None
    tokens_input: int = Field(default=0, ge=0)
    tokens_output: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"))
    latency_ms: int = Field(default=0, ge=0)
    has_pending_order_draft: bool = Field(
        default=False,
        description="True when the assistant turn carries an unconfirmed order draft.",
    )
    has_pending_report_draft: bool = Field(
        default=False,
        description="True when the assistant turn carries an unconfirmed report draft.",
    )
    created_at: datetime


class ConversationListItem(BaseModel):
    """One row in the conversation rail."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str | None
    last_message_excerpt: str | None = Field(
        default=None,
        description="First ~80 chars of the latest assistant or user turn.",
    )
    active_account_name: str | None = None
    turn_count: int = Field(..., ge=0)
    updated_at: datetime


class ConversationOut(BaseModel):
    """Read shape for a single conversation with its turns hydrated."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    salesperson_id: uuid.UUID | None
    active_account_id: uuid.UUID | None
    active_account_name: str | None = None
    title: str | None
    model_used: str | None
    created_at: datetime
    updated_at: datetime
    turns: list[ChatTurnOut] = Field(default_factory=list)


class ConversationListOut(BaseModel):
    """Paginated list of conversations."""

    model_config = ConfigDict(frozen=True)

    items: list[ConversationListItem]
    total: int = Field(..., ge=0)


# ---------------------------------------------------------------------------
# Confirm-result shapes (returned by the confirm endpoints)
# ---------------------------------------------------------------------------


class ConfirmResultOut(BaseModel):
    """Result of a confirm-and-persist call.

    Returned by ``POST /api/copilot/turns/{id}/confirm-order`` and
    ``confirm-report``. The HTMX route variant returns a small partial
    instead, but for the JSON API this is the canonical shape.
    """

    model_config = ConfigDict(frozen=True)

    ok: bool
    persisted_id: uuid.UUID | None = Field(
        default=None,
        description="Primary key of the row written (orders.id or customer_reports.id).",
    )
    message: str = Field(
        ...,
        description="Human-readable status — surfaced to the user inline.",
    )
