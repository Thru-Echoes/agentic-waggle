"""Pydantic I/O schemas for the sales copilot.

These are the API-side mirrors of the SQLModel ``Conversation`` /
``ChatTurn`` tables (see ``db/models.py``). The DB models are storage
shape; these are what crosses the chat orchestrator's boundaries —
intent routing, context building, tool calls, and the final assistant
response.

Anything the LLM is asked to fill inherits ``Grounded`` (see
``agents/schemas.py``) so evidence + confidence are forced fields.

Confidence is backend-only and never rendered to users.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.schemas import Grounded, OrderDraft


# ---------------------------------------------------------------------------
# Tool call records
# ---------------------------------------------------------------------------


class ToolCallRecord(BaseModel):
    """One tool invocation made by the copilot during a chat turn.

    Stored under ``ChatTurn.tool_calls`` as JSONB. Capped at the result
    excerpt (4K chars) so conversation history doesn't balloon when a
    tool returns a large payload — we keep enough for the LLM to follow
    the thread on the next turn.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(..., description="Name of the tool that was called.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments the LLM passed (already validated against the tool's schema).",
    )
    result_excerpt: str = Field(
        ...,
        max_length=4096,
        description=(
            "First 4K chars of the tool's result. Older turns are "
            "summarized further in history truncation; this is the "
            "first-level cap."
        ),
    )
    latency_ms: int = Field(..., ge=0, description="Wall-clock time for the tool call.")
    tokens: int = Field(default=0, ge=0, description="Tokens charged by the LLM that drove this tool call (0 if N/A).")


# ---------------------------------------------------------------------------
# Chat turns
# ---------------------------------------------------------------------------


ConversationTurnRole = Literal["user", "assistant", "system", "tool"]


class ChatTurnIn(BaseModel):
    """User-supplied input for a new chat turn.

    The route handler builds a full ``ChatTurn`` (DB row) from this plus
    the orchestrator's response.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(..., min_length=1, max_length=8_000)
    audio_blob_id: str | None = Field(
        default=None,
        description=(
            "When the input came from dictation, the storage key of the "
            "uploaded audio. The route transcribes it via Whisper and "
            "writes ``text`` from the transcript."
        ),
    )


class ChatTurnRead(BaseModel):
    """A persisted chat turn rendered back to the client.

    Confidence and per-token metrics are deliberately absent from the
    public read shape — backend-only. Tool-call records and evidence
    quotes are included because the UI surfaces them inline.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: ConversationTurnRole
    text: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    created_at: datetime


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class ConversationRead(BaseModel):
    """A conversation's public shape, with N most recent turns hydrated.

    Used by ``GET /copilot/<id>`` and the conversation list on the left
    rail of ``/copilot``.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    salesperson_id: uuid.UUID | None
    active_account_id: uuid.UUID | None
    title: str | None
    model_used: str | None
    created_at: datetime
    updated_at: datetime
    turns: list[ChatTurnRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Intent routing (LLM-filled, Grounded)
# ---------------------------------------------------------------------------


CopilotIntent = Literal[
    "query",                  # ask a question about a customer / report / order
    "log_customer_report",    # dictate or type a new customer-visit report
    "create_order",           # draft a new order from a description
    "command",                # automation (price change, status update, etc.)
    "unknown",                # router can't classify; orchestrator falls back to query
]


class IntentRoute(Grounded):
    """The intent router's structured output.

    Single fast-model call: classify the user's input and pull any
    explicitly mentioned account / customer hint. Evidence cites the
    1-2 verbatim phrases that justify the routing.
    """

    intent: CopilotIntent = Field(..., description="What the user wants to do this turn.")
    target_account_hint: str | None = Field(
        default=None,
        description=(
            "Verbatim customer name or hint from the user's message, if "
            "any. The deterministic resolver maps this to a customer_id."
        ),
    )
    reasoning: str = Field(..., max_length=300, description="One short sentence.")


# ---------------------------------------------------------------------------
# Customer report / order drafts produced via the copilot
# ---------------------------------------------------------------------------


class CustomerReportDraft(Grounded):
    """Structured extraction from a dictated or typed customer-visit report.

    Used for the ``log_customer_report`` intent. The salesperson reviews
    the draft before it persists to ``customer_reports``.
    """

    visit_date: datetime | None = Field(
        default=None,
        description="ISO date of the visit if explicit; null otherwise.",
    )
    customer_id_hint: str | None = Field(
        default=None,
        description=(
            "Customer name (verbatim) the report is about. The orchestrator "
            "resolves this against the DB before the row is persisted."
        ),
    )
    varroa_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Varroa mite percentage if explicitly stated.",
    )
    observations: list[str] = Field(
        default_factory=list,
        description="Plain-language observations (queen health, hive condition, etc.).",
    )
    action_items: list[str] = Field(
        default_factory=list,
        description="Concrete next steps the salesperson said they'd take.",
    )


# ---------------------------------------------------------------------------
# Citations (rendered inline under copilot responses)
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A pointer to the source row that grounded part of a copilot answer.

    Rendered as a clickable chip under the assistant text. Click takes
    the user to the underlying customer report / order / email.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["customer_report", "order", "email", "customer", "product"]
    id: uuid.UUID
    label: str = Field(..., description="Short human-readable label for the chip.")
    excerpt: str = Field(
        ...,
        max_length=400,
        description="Brief snippet from the source that supports the assertion.",
    )


# ---------------------------------------------------------------------------
# Final copilot response (the orchestrator's terminal output)
# ---------------------------------------------------------------------------


class CopilotResponse(BaseModel):
    """The orchestrator's response for one chat turn.

    Bundles the assistant text, the citations the UI renders as chips,
    and the per-turn metrics that get persisted to ChatTurn. Confidence
    fields stay on the underlying ``IntentRoute`` / ``CustomerReportDraft``
    instances — they're not surfaced here.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(..., description="The assistant's natural-language reply.")
    citations: list[Citation] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    model: str = Field(..., description="Model that produced this response.")
    tokens_input: int = Field(default=0, ge=0)
    tokens_output: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"))
    latency_ms: int = Field(default=0, ge=0)
    # Optional draft payloads — present when the orchestrator successfully
    # extracted an order or visit-report draft for the user to confirm.
    # The route layer stores these inside the assistant ChatTurn's
    # ``tool_calls`` JSONB so the confirm endpoint can re-hydrate the draft
    # by id without a separate drafts table.
    pending_order_draft: OrderDraft | None = Field(
        default=None,
        description=(
            "Order draft awaiting user confirmation. Persisted to the assistant "
            "turn's tool_calls JSONB; confirm-order route re-hydrates and writes "
            "the orders row."
        ),
    )
    pending_report_draft: CustomerReportDraft | None = Field(
        default=None,
        description=(
            "Customer-visit report draft awaiting confirmation. Persisted to "
            "the assistant turn's tool_calls JSONB; confirm-report route "
            "re-hydrates and writes the customer_reports row."
        ),
    )
    resolved_customer_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Customer the orchestrator anchored on for this turn. Drives the "
            "active-account indicator + Conversation.active_account_id update."
        ),
    )
    pending_customer_match: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Set when the semantic matcher returned low or medium confidence. "
            "The route layer appends this as a flat JSONB sentinel with keys "
            "tool_name, target_intent, hint, alternatives so the "
            "confirm-match route can resume the paused turn. Backend-only."
        ),
    )
