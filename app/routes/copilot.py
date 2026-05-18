"""Sales-copilot routes (HTML + HTMX + JSON API).

Surfaces in this module:

    GET  /copilot                                 — chat shell, auto-selects newest conv
    GET  /copilot/{conversation_id}               — chat shell, anchored on a specific conv
    POST /copilot/turns                           — create user turn → orchestrator → render assistant
    POST /copilot/conversations                   — new empty conversation, redirect
    POST /copilot/turns/{turn_id}/confirm-order   — persist a stashed OrderDraft → orders row
    POST /copilot/turns/{turn_id}/confirm-report  — persist a stashed CustomerReportDraft
    POST /copilot/turns/{turn_id}/confirm-match   — resume a paused turn after customer pick
    GET  /copilot/rail                            — re-render conversation list partial

    GET  /api/copilot/conversations               — JSON list (programmatic clients)
    GET  /api/copilot/conversations/{id}          — JSON conversation read (programmatic)

Persistence flow per turn:
    1. Insert a ChatTurn row for the user message
    2. Run the orchestrator (1-3 LLM calls + DB lookups)
    3. Insert a ChatTurn row for the assistant reply, with metrics +
       tool-call records + (when applicable) a synthetic ``pending_draft``
       record stashing the OrderDraft / CustomerReportDraft so the confirm
       route can re-hydrate it.
    4. Update Conversation.updated_at + active_account_id, auto-title on
       the first turn.

The HTMX flow returns rendered partials; the JSON API returns the
canonical Pydantic shapes from ``app/schemas/copilot.py``.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select, text
from sqlmodel import Session

from agents.copilot.conversation_schemas import (
    CopilotResponse,
    CustomerReportDraft,
    ToolCallRecord,
)
from agents.copilot.orchestrator import get_copilot_response
from agents.order_extractor import _build_chat_model
from agents.schemas import OrderDraft
from app.config import Settings, get_settings
from app.routes._order_persistence import (
    apply_order_overrides,
    persist_order_from_draft,
    resolve_default_apiarist,
)
from app.schemas.copilot import (
    ChatTurnOut,
    CitationOut,
    ConfirmResultOut,
    ConversationCreateIn,
    ConversationListItem,
    ConversationListOut,
    ConversationOut,
    ToolCallOut,
)
from db.models import (
    ChatTurn,
    Conversation,
    Customer,
    CustomerReport,
    CustomerSite,
    utc_now,
)
from db.session import get_db_session

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Marker tool_name used to stash an OrderDraft on the assistant ChatTurn's
#: ``tool_calls`` JSONB column. The confirm-order endpoint re-hydrates from
#: this record. Kept distinct from real tool names so the UI can split them.
_PENDING_ORDER_TOOL_NAME = "pending_order_draft"

#: Same idea for CustomerReportDraft.
_PENDING_REPORT_TOOL_NAME = "pending_report_draft"

#: Fallback character cap on the auto-generated conversation title.
_TITLE_FALLBACK_LIMIT = 60

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ---------------------------------------------------------------------------
# Routers — one for HTML/HTMX (no schema), one for JSON API
# ---------------------------------------------------------------------------

# HTML/HTMX router. Mounted at root (no prefix). include_in_schema=False so
# the partials don't pollute the OpenAPI doc.
html_router = APIRouter(tags=["copilot-pages"], include_in_schema=False)

# JSON API router. Mounted under /api in app.main.
api_router = APIRouter(prefix="/copilot", tags=["copilot"])


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _common_context(request: Request, settings: Settings) -> dict:
    """Vars every base.html template expects."""
    return {
        "request": request,
        "product": settings.product_name,
        "tagline": settings.tagline,
        "company": settings.company_name,
    }


def _build_models(settings: Settings) -> dict[str, Any]:
    """Construct the per-stage chat models for the orchestrator.

    Side effects: instantiates 3 LangChain chat clients. They're cheap to
    build (no network) but we still rebuild per request — caching is a
    later polish if it shows up in profiling.
    """
    return {
        "router": _build_chat_model(settings.copilot_router_model),
        "main": _build_chat_model(settings.copilot_main_model),
        "report": _build_chat_model(settings.copilot_report_model),
    }


def _list_conversations(db: Session, limit: int = 50) -> list[ConversationListItem]:
    """Fetch conversation rail items, newest-first.

    Side effects: 4 SELECTs (conversations, turn counts, latest turns,
    active-account names). Multi-column queries use raw SQL via ``text()``
    to match the project's existing pattern in ``agents/copilot/``.
    """
    rows = db.execute(
        text(
            """
            SELECT id, title, active_account_id, updated_at
            FROM conversations
            ORDER BY updated_at DESC
            LIMIT :lim
            """
        ),
        {"lim": limit},
    ).mappings().all()
    if not rows:
        return []

    conv_ids = [_coerce_uuid(r["id"]) for r in rows]

    # Per-conversation turn counts.
    turn_counts: dict[uuid.UUID, int] = {}
    count_rows = db.execute(
        text(
            """
            SELECT conversation_id, COUNT(*) AS cnt
            FROM chat_turns
            WHERE conversation_id = ANY(CAST(:ids AS uuid[]))
            GROUP BY conversation_id
            """
        ),
        {"ids": [str(cid) for cid in conv_ids]},
    ).mappings().all()
    for cr in count_rows:
        turn_counts[_coerce_uuid(cr["conversation_id"])] = int(cr["cnt"])

    # Per-conversation latest turn text (for the rail excerpt).
    latest_per_conv: dict[uuid.UUID, str] = {}
    latest_rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (conversation_id)
                   conversation_id, text
            FROM chat_turns
            WHERE conversation_id = ANY(CAST(:ids AS uuid[]))
            ORDER BY conversation_id, created_at DESC
            """
        ),
        {"ids": [str(cid) for cid in conv_ids]},
    ).mappings().all()
    for lr in latest_rows:
        latest_per_conv[_coerce_uuid(lr["conversation_id"])] = lr["text"] or ""

    # Customer names for any active accounts referenced.
    active_account_ids: set[uuid.UUID] = {
        _coerce_uuid(r["active_account_id"])
        for r in rows
        if r["active_account_id"]
    }
    name_by_id: dict[uuid.UUID, str] = {}
    if active_account_ids:
        name_rows = db.execute(
            text(
                "SELECT id, name FROM customers "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [str(cid) for cid in active_account_ids]},
        ).mappings().all()
        for nr in name_rows:
            name_by_id[_coerce_uuid(nr["id"])] = nr["name"]

    items: list[ConversationListItem] = []
    for r in rows:
        cid = _coerce_uuid(r["id"])
        excerpt = latest_per_conv.get(cid, "")
        if excerpt and len(excerpt) > 80:
            excerpt = excerpt[:80].rstrip() + "…"
        active_id = (
            _coerce_uuid(r["active_account_id"])
            if r["active_account_id"]
            else None
        )
        items.append(
            ConversationListItem(
                id=cid,
                title=r["title"],
                last_message_excerpt=excerpt or None,
                active_account_name=(
                    name_by_id.get(active_id) if active_id else None
                ),
                turn_count=turn_counts.get(cid, 0),
                updated_at=r["updated_at"],
            )
        )
    return items


def _coerce_uuid(val: Any) -> uuid.UUID:
    """Coerce a string-or-UUID DB value to a uuid.UUID."""
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _get_or_create_conversation(
    db: Session, conversation_id: uuid.UUID | None
) -> Conversation:
    """Return the requested conversation, the newest, or a freshly-created one.

    Side effects: SELECT (and possibly INSERT) against ``conversations``.
    """
    if conversation_id is not None:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conv

    row = db.execute(
        text(
            "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
        )
    ).mappings().first()
    if row is not None:
        existing = db.get(Conversation, _coerce_uuid(row["id"]))
        if existing is not None:
            return existing

    conv = Conversation()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _last_3_customer_ids(
    db: Session, conversation_id: uuid.UUID
) -> list[uuid.UUID]:
    """Return the three most-recently-resolved customer IDs in a conversation.

    Scans assistant turns in reverse-chronological order and collects
    distinct customer IDs from ``match_customer`` tool_call records.
    Used to populate ``ConversationContext.last_3_customer_ids`` for the
    semantic matcher's recency boost.

    Side effects: one SELECT on chat_turns.
    """
    rows = db.execute(
        text(
            """
            SELECT tool_calls
            FROM chat_turns
            WHERE conversation_id = :cid AND role = 'assistant'
            ORDER BY created_at DESC
            LIMIT 20
            """
        ),
        {"cid": str(conversation_id)},
    ).mappings().all()

    seen: list[uuid.UUID] = []
    for row in rows:
        for tc in row.get("tool_calls") or []:
            if tc.get("tool_name") == "match_customer":
                cid_str = tc.get("customer_id")
                if cid_str:
                    try:
                        cid = uuid.UUID(cid_str)
                    except ValueError:
                        continue
                    if cid not in seen:
                        seen.append(cid)
            if len(seen) >= 3:
                break
        if len(seen) >= 3:
            break
    return seen[:3]


def _load_turns(db: Session, conversation_id: uuid.UUID) -> list[ChatTurn]:
    """Fetch all turns for a conversation in chronological order."""
    rows = db.execute(
        text(
            """
            SELECT id FROM chat_turns
            WHERE conversation_id = :cid
            ORDER BY created_at
            """
        ),
        {"cid": str(conversation_id)},
    ).mappings().all()
    out: list[ChatTurn] = []
    for r in rows:
        t = db.get(ChatTurn, _coerce_uuid(r["id"]))
        if t is not None:
            out.append(t)
    return out


def _split_tool_calls(
    raw: list[dict[str, Any]],
) -> tuple[list[ToolCallOut], dict[str, Any] | None, dict[str, Any] | None]:
    """Separate synthetic pending-draft records from real tool calls.

    Returns ``(real_tool_calls, pending_order_dict, pending_report_dict)``.
    The dicts are the raw ``arguments.draft_data`` payloads; callers
    validate them back into the corresponding Pydantic schema.
    """
    real: list[ToolCallOut] = []
    pending_order: dict[str, Any] | None = None
    pending_report: dict[str, Any] | None = None
    for record in raw or []:
        name = record.get("tool_name", "")
        if name == _PENDING_ORDER_TOOL_NAME:
            pending_order = (record.get("arguments") or {}).get("draft_data")
        elif name == _PENDING_REPORT_TOOL_NAME:
            pending_report = (record.get("arguments") or {}).get("draft_data")
        else:
            real.append(ToolCallOut.model_validate(record))
    return real, pending_order, pending_report


def _turn_to_out(turn: ChatTurn) -> ChatTurnOut:
    """Convert a DB ChatTurn row to the API ChatTurnOut shape.

    Splits synthetic pending-draft tool-call records out of the public
    ``tool_calls`` list so they don't render as "tool ran" chips in the UI.
    """
    real_tools, pending_order, pending_report = _split_tool_calls(turn.tool_calls)
    return ChatTurnOut(
        id=turn.id,
        conversation_id=turn.conversation_id,
        role=turn.role,  # type: ignore[arg-type]
        text=turn.text,
        tool_calls=real_tools,
        evidence_used=list(turn.evidence_used or []),
        citations=[],  # Citations live in the orchestrator's response shape; not persisted yet.
        model=turn.model,
        tokens_input=turn.tokens_input,
        tokens_output=turn.tokens_output,
        cost_usd=turn.cost_usd,
        latency_ms=turn.latency_ms,
        has_pending_order_draft=pending_order is not None,
        has_pending_report_draft=pending_report is not None,
        created_at=turn.created_at,
    )


def _turn_view_for_template(turn: ChatTurn) -> dict[str, Any]:
    """Pack a turn into a dict the Jinja partials render directly.

    Templates can't easily call Pydantic methods, so we pre-rehydrate the
    pending drafts here. The result has both the public ChatTurnOut fields
    (so style is consistent) plus the optional ``pending_order_draft`` and
    ``pending_report_draft`` Pydantic instances ready for rendering.
    """
    real_tools, pending_order_data, pending_report_data = _split_tool_calls(
        turn.tool_calls
    )
    pending_order: OrderDraft | None = None
    if pending_order_data is not None:
        pending_order = OrderDraft.model_validate(pending_order_data)
    pending_report: CustomerReportDraft | None = None
    if pending_report_data is not None:
        pending_report = CustomerReportDraft.model_validate(pending_report_data)
    return {
        "id": turn.id,
        "role": turn.role,
        "text": turn.text,
        "tool_calls": real_tools,
        "evidence_used": list(turn.evidence_used or []),
        "model": turn.model,
        "latency_ms": turn.latency_ms,
        "created_at": turn.created_at,
        "pending_order_draft": pending_order,
        "pending_report_draft": pending_report,
    }


def _persist_assistant_turn(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    response: CopilotResponse,
) -> ChatTurn:
    """Insert the assistant ChatTurn row, stashing any pending drafts.

    Side effects: one INSERT on ``chat_turns``.
    """
    tool_call_records: list[dict[str, Any]] = [
        tc.model_dump(mode="json") for tc in response.tool_calls
    ]
    if response.pending_order_draft is not None:
        tool_call_records.append({
            "tool_name": _PENDING_ORDER_TOOL_NAME,
            "arguments": {
                "draft_data": response.pending_order_draft.model_dump(mode="json"),
            },
            "result_excerpt": (
                f"order draft for "
                f"{response.pending_order_draft.customer.customer_name}, "
                f"{len(response.pending_order_draft.line_items)} items, "
                f"${response.pending_order_draft.total_usd}"
            )[:4096],
            "latency_ms": 0,
            "tokens": 0,
        })
    if response.pending_report_draft is not None:
        tool_call_records.append({
            "tool_name": _PENDING_REPORT_TOOL_NAME,
            "arguments": {
                "draft_data": response.pending_report_draft.model_dump(mode="json"),
            },
            "result_excerpt": (
                f"report draft varroa={response.pending_report_draft.varroa_pct} "
                f"observations={len(response.pending_report_draft.observations)}"
            )[:4096],
            "latency_ms": 0,
            "tokens": 0,
        })
    # Flat sentinel — stored with target_intent, hint, alternatives at the
    # top level so _extract_pending_match / resume_with_customer can read
    # them with a single tc.get() call (no nesting).
    if response.pending_customer_match is not None:
        tool_call_records.append(response.pending_customer_match)

    turn = ChatTurn(
        conversation_id=conversation_id,
        role="assistant",
        text=response.text,
        tool_calls=tool_call_records,
        evidence_used=list(response.evidence_used),
        model=response.model,
        tokens_input=response.tokens_input,
        tokens_output=response.tokens_output,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
    )
    db.add(turn)
    db.commit()
    db.refresh(turn)
    return turn


def _maybe_title_conversation(
    conv: Conversation,
    first_user_text: str,
    db: Session,
    settings: Settings,
) -> None:
    """Set Conversation.title on the first user turn if not already set.

    Uses an LLM (Settings.copilot_title_model) to summarize the first user
    message into a 4-6 word title. Falls back to the deterministic 60-char
    truncation if the LLM call fails or returns something unusable. Empty
    titles fall through to the literal "Untitled conversation" string so
    the rail never shows nothing.

    The LLM call is small (single-prompt, ~10 tokens output) — adds
    roughly half a second to the first turn. The cost saves us from
    "What's the general state of varroa across our customers?" being
    shown verbatim in the rail.

    Side effects: one LLM call + UPDATE on ``conversations`` if title is set.
    """
    if conv.title:
        return
    fallback = _deterministic_title(first_user_text)
    title = _llm_title(first_user_text, settings) or fallback
    conv.title = title
    db.add(conv)
    db.commit()


def _deterministic_title(first_user_text: str) -> str:
    """Cheap fallback title — first line of the user's text, capped at 60 chars."""
    title = first_user_text.strip().splitlines()[0] if first_user_text.strip() else ""
    if not title:
        return "Untitled conversation"
    if len(title) > _TITLE_FALLBACK_LIMIT:
        return title[:_TITLE_FALLBACK_LIMIT].rstrip() + "…"
    return title


_TITLE_PROMPT = """\
Summarize this user message into a 4-6 word conversation title. The
title should be short, descriptive, and capture the topic — like a
ChatGPT conversation title.

Examples:
- "What's the varroa trend at O'Reilly?" → "Varroa trend at O'Reilly"
- "Draft an order for St. Agnes — 40 hives." → "St. Agnes order draft"
- "Visited Cascade today, mites at 3.5%" → "Cascade visit report"

Message:
{text}

Return ONLY the title, no quotes, no punctuation at the end, no extra
text. Max 60 characters.
"""


def _llm_title(text_in: str, settings: Settings) -> str | None:
    """One-shot LLM call to summarize the first user turn into a short title.

    Returns the trimmed title, or ``None`` if the call fails / returns
    something unusable (so the caller can fall through to the
    deterministic fallback).

    Side effects: one LLM call (~10 tokens output).
    """
    try:
        chat_model = _build_chat_model(settings.copilot_title_model)
        prompt = _TITLE_PROMPT.format(text=text_in)
        result = chat_model.invoke(prompt)
        raw = getattr(result, "content", None)
        if isinstance(raw, list):
            raw = "".join(
                str(b.get("text", "")) if isinstance(b, dict) else str(b)
                for b in raw
            )
        if not isinstance(raw, str):
            return None
        title = raw.strip().strip('"').strip("'").rstrip(".").strip()
        if not title:
            return None
        # Cap length defensively — the prompt says max 60 but models drift.
        if len(title) > _TITLE_FALLBACK_LIMIT:
            title = title[:_TITLE_FALLBACK_LIMIT].rstrip() + "…"
        return title
    except Exception as exc:
        log.warning("title LLM call failed: %s — using fallback", exc)
        return None


def _resolve_active_customer_site(
    db: Session, customer_id: uuid.UUID
) -> CustomerSite:
    """Pick the first customer site for a customer (deterministic).

    Used when persisting a confirmed CustomerReportDraft — the draft
    doesn't carry a site reference, so we attach it to the customer's
    earliest-named site.

    Raises ``LookupError`` if the customer has no sites.
    """
    row = db.execute(
        text(
            """
            SELECT id FROM customer_sites
            WHERE customer_id = :cid
            ORDER BY name
            LIMIT 1
            """
        ),
        {"cid": str(customer_id)},
    ).mappings().first()
    if row is None:
        raise LookupError(
            f"Customer {customer_id} has no customer_sites. Cannot attach a "
            f"customer report; the seed pipeline must have skipped a step."
        )
    site = db.get(CustomerSite, _coerce_uuid(row["id"]))
    if site is None:
        raise LookupError("CustomerSite vanished between SELECT and ORM load.")
    return site


# ---------------------------------------------------------------------------
# Internal-only render routes (template testing)
# ---------------------------------------------------------------------------


@html_router.get("/_internal/render/confirm-match", response_class=HTMLResponse)
async def _internal_render_confirm_match(
    request: Request,
    turn_id: uuid.UUID,
    customer_id: uuid.UUID,
    customer_name: str,
) -> HTMLResponse:
    """Test-only route. Renders the confirm-match partial in isolation."""
    return templates.TemplateResponse(
        request,
        "partials/copilot_confirm_match.html",
        {"turn_id": turn_id, "customer_id": customer_id,
         "customer_name": customer_name},
    )


@html_router.get("/_internal/render/pick-match", response_class=HTMLResponse)
async def _internal_render_pick_match(
    request: Request,
    turn_id: uuid.UUID,
    alt: list[str] = Query(default_factory=list),
) -> HTMLResponse:
    """Test-only route. Renders the pick-match partial.

    Each `alt` query param is `customer_id|customer_name`.
    """
    alternatives = []
    for a in alt:
        cid, name = a.split("|", 1)
        alternatives.append({"customer_id": cid, "customer_name": name, "confidence": None})
    return templates.TemplateResponse(
        request,
        "partials/copilot_pick_match.html",
        {"turn_id": turn_id, "alternatives": alternatives},
    )


# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------


@html_router.get("/copilot", response_class=HTMLResponse)
def copilot_index(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Render the copilot shell anchored on the most recent conversation.

    If no conversations exist, creates a fresh one so the composer is
    immediately usable.
    """
    conv = _get_or_create_conversation(db, None)
    return _render_shell(request, settings, db, conv)


# NOTE: /copilot/_rail is declared BEFORE /copilot/{conversation_id} so the
# UUID catch-all doesn't shadow it. Same pattern for any future GET routes
# under /copilot — keep them above the conversation-id route.
@html_router.get("/copilot/_rail", response_class=HTMLResponse)
def copilot_rail(
    request: Request,
    db: Session = Depends(get_db_session),
    active_id: uuid.UUID | None = None,
) -> HTMLResponse:
    """Re-render the conversation list (used after creating a new conv)."""
    rail = _list_conversations(db)
    return templates.TemplateResponse(
        request,
        "partials/copilot_rail.html",
        {"rail": rail, "active_conversation_id": active_id},
    )


@html_router.get("/copilot/{conversation_id}", response_class=HTMLResponse)
def copilot_for_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Render the copilot shell for one specific conversation."""
    conv = _get_or_create_conversation(db, conversation_id)
    return _render_shell(request, settings, db, conv)


def _render_shell(
    request: Request,
    settings: Settings,
    db: Session,
    conv: Conversation,
) -> HTMLResponse:
    """Render templates/copilot.html with full thread + rail context."""
    turns = _load_turns(db, conv.id)
    rail = _list_conversations(db)
    active_account_name: str | None = None
    if conv.active_account_id:
        cust = db.get(Customer, conv.active_account_id)
        if cust is not None:
            active_account_name = cust.name
    ctx = _common_context(request, settings) | {
        "nav": "copilot",
        "conversation": conv,
        "turns": [_turn_view_for_template(t) for t in turns],
        "rail": rail,
        "active_account_name": active_account_name,
    }
    return templates.TemplateResponse(request, "copilot.html", ctx)


# ---------------------------------------------------------------------------
# HTMX action: post a new turn
# ---------------------------------------------------------------------------


@html_router.post("/copilot/turns")
async def post_turn(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    conversation_id: uuid.UUID = Form(...),
    text: str = Form(..., min_length=1, max_length=8_000),
):
    """Create a user turn → run orchestrator → persist + return both partials.

    Content negotiation: when the client sends ``Accept: text/event-stream``
    (set by the browser composer for live status updates), this returns
    Server-Sent Events streaming user_turn → status… → complete. Otherwise
    it returns the canonical HTML partial (used by tests, programmatic
    clients, and as a graceful fallback when SSE isn't available).

    Side effects:
      * INSERT user ChatTurn
      * 1-3 LLM calls + DB SELECTs via the orchestrator
      * INSERT assistant ChatTurn
      * UPDATE Conversation.updated_at + active_account_id (and title on
        first turn)
    """
    conv = _get_or_create_conversation(db, conversation_id)

    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return StreamingResponse(
            _stream_turn_events(
                conv=conv, text=text, db=db, settings=settings,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx-style proxy buffering
                "Connection": "keep-alive",
            },
        )

    # 1. Persist user turn first so the new row is visible if the LLM call
    # raises — fail-loud: the user message stays, the assistant slot stays
    # empty, the user can retry.
    user_turn = ChatTurn(
        conversation_id=conv.id,
        role="user",
        text=text,
        tool_calls=[],
        evidence_used=[],
        tokens_input=0,
        tokens_output=0,
        cost_usd=Decimal("0"),
        latency_ms=0,
    )
    db.add(user_turn)
    db.commit()
    db.refresh(user_turn)

    # 2. First-turn title.
    _maybe_title_conversation(conv, text, db, settings)

    # 3. Run the orchestrator.
    models = _build_models(settings)
    response = get_copilot_response(
        user_text=text,
        db=db,
        models=models,
        conv_id=conv.id,
        active_account_id=conv.active_account_id,
        last_3_customer_ids=_last_3_customer_ids(db, conv.id),
    )

    # 4. Persist assistant turn (with any stashed drafts).
    assistant_turn = _persist_assistant_turn(
        db, conversation_id=conv.id, response=response
    )

    # 5. Sync conversation metadata.
    conv.updated_at = utc_now()
    if response.resolved_customer_id is not None:
        conv.active_account_id = response.resolved_customer_id
    conv.model_used = response.model
    db.add(conv)
    db.commit()

    # 6. Render both partials concatenated — HTMX appends to #thread.
    user_view = _turn_view_for_template(user_turn)
    assistant_view = _turn_view_for_template(assistant_turn)
    return templates.TemplateResponse(
        request,
        "partials/copilot_turn_pair.html",
        {
            "user_turn": user_view,
            "assistant_turn": assistant_view,
            "conversation": conv,
        },
    )


# ---------------------------------------------------------------------------
# Streaming variant — Server-Sent Events for live status updates
# ---------------------------------------------------------------------------


#: User-facing labels for each orchestrator stage. The streaming SSE
#: branch shows these as "thinking..." status messages while the LLM
#: work happens. Stages the orchestrator emits but that aren't in this
#: dict (e.g. ``account_resolved``) are silently skipped.
_STAGE_LABELS: dict[str, str] = {
    "intent_routed": "Routed your intent",
    "context_built": "Loaded customer context",
    "answer_drafting": "Drafting your answer",
    "answer_ready": "Answer ready",
    "draft_ready": "Building the draft card",
}


def _sse_event(name: str, html: str) -> bytes:
    """Format an HTML payload as a single SSE event.

    SSE ``data:`` is single-line by default; multi-line payloads need
    each line prefixed. We strip-then-rejoin so the rendered HTML
    survives the framing.
    """
    lines = html.strip().splitlines()
    if not lines:
        return f"event: {name}\ndata: \n\n".encode("utf-8")
    body = "\n".join(f"data: {line}" for line in lines)
    return f"event: {name}\n{body}\n\n".encode("utf-8")


async def _stream_turn_events(
    *,
    conv: Conversation,
    text: str,
    db: Session,
    settings: Settings,
) -> AsyncIterator[bytes]:
    """Async generator that emits SSE events for one chat turn.

    Sequence of events:
      1. ``user_turn`` — HTML for the user bubble, immediately
      2. ``status`` × N — one per orchestrator stage label
      3. ``complete`` — HTML for the assistant bubble (final swap)

    The orchestrator runs inside ``asyncio.to_thread`` so the event loop
    can keep draining the status queue while it works. The request-scoped
    ``db`` session is used from the worker thread, which is safe because
    the call is serialized — no concurrent access.

    Side effects: INSERT user + assistant ChatTurns, UPDATE Conversation
    metadata, 1-3 LLM calls.
    """
    # 1. Persist user turn first so the new row exists even if the LLM
    # work raises.
    user_turn = ChatTurn(
        conversation_id=conv.id,
        role="user",
        text=text,
        tool_calls=[],
        evidence_used=[],
        tokens_input=0,
        tokens_output=0,
        cost_usd=Decimal("0"),
        latency_ms=0,
    )
    db.add(user_turn)
    db.commit()
    db.refresh(user_turn)

    _maybe_title_conversation(conv, text, db, settings)

    # Emit user bubble immediately.
    user_html = templates.get_template("partials/copilot_turn_user.html").render(
        turn=_turn_view_for_template(user_turn),
    )
    yield _sse_event("user_turn", user_html)
    yield _sse_event("status", '<span class="thinking-dot"></span> Thinking…')

    # 2. Set up the progress queue + run orchestrator in a thread.
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def _on_step(stage: str, payload: Any) -> None:
        try:
            queue.put_nowait((stage, payload))
        except asyncio.QueueFull:
            pass

    models = _build_models(settings)
    _conv_last_3 = _last_3_customer_ids(db, conv.id)

    def _run_sync() -> CopilotResponse:
        return get_copilot_response(
            user_text=text,
            db=db,
            models=models,
            conv_id=conv.id,
            active_account_id=conv.active_account_id,
            last_3_customer_ids=_conv_last_3,
            on_step=_on_step,
        )

    orch_task = asyncio.create_task(asyncio.to_thread(_run_sync))

    # Drain status events as they arrive. Poll the queue with a short
    # timeout so we wake up to check whether the task is done.
    while True:
        if orch_task.done() and queue.empty():
            break
        try:
            stage, _payload = await asyncio.wait_for(queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            continue
        label = _STAGE_LABELS.get(stage)
        if label is None:
            continue
        yield _sse_event(
            "status",
            f'<span class="thinking-dot"></span> {label}…',
        )

    # 3. Surface any orchestrator exception as a friendly status, then
    # close the stream. (Don't let exceptions bubble through SSE — the
    # client gets a malformed stream otherwise.)
    try:
        response = await orch_task
    except Exception as exc:
        log.exception("orchestrator failed mid-stream")
        yield _sse_event(
            "complete",
            (
                '<div class="turn assistant"><div class="bubble">'
                '<div class="text">Something went wrong while drafting the '
                f'answer ({type(exc).__name__}). Please retry.</div>'
                "</div></div>"
            ),
        )
        return

    # 4. Persist the assistant turn + sync conversation metadata.
    assistant_turn = _persist_assistant_turn(
        db, conversation_id=conv.id, response=response,
    )
    conv.updated_at = utc_now()
    if response.resolved_customer_id is not None:
        conv.active_account_id = response.resolved_customer_id
    conv.model_used = response.model
    db.add(conv)
    db.commit()

    # 5. Final swap: full assistant bubble HTML.
    assistant_html = templates.get_template("partials/copilot_turn_assistant.html").render(
        turn=_turn_view_for_template(assistant_turn),
    )
    yield _sse_event("complete", assistant_html)


# ---------------------------------------------------------------------------
# HTMX action: create new conversation
# ---------------------------------------------------------------------------


@html_router.post("/copilot/conversations", response_class=HTMLResponse)
def post_new_conversation(
    request: Request,
    db: Session = Depends(get_db_session),
) -> RedirectResponse:
    """Create an empty conversation and redirect the browser into it."""
    conv = Conversation()
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return RedirectResponse(url=f"/copilot/{conv.id}", status_code=303)


# ---------------------------------------------------------------------------
# HTMX action: confirm-and-persist drafts
# ---------------------------------------------------------------------------


@html_router.post(
    "/copilot/turns/{turn_id}/confirm-order", response_class=HTMLResponse
)
async def confirm_order(
    turn_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    """Persist a stashed OrderDraft as a real ``orders`` row + line items.

    Reads any user-edited overrides from the form body (``qty_{i}``,
    ``unit_price_{i}``, ``pollination_date``) so the salesperson can
    correct LLM mistakes before saving. The overrides are layered onto
    the stashed draft via ``apply_order_overrides``.

    Side effects: INSERT on orders, order_line_items, order_state_transitions.
    """
    form = await request.form()
    overrides: dict[str, str] = {
        k: v for k, v in form.items() if isinstance(v, str)
    }
    result = _persist_order_draft(
        turn_id=turn_id, db=db, overrides=overrides,
    )
    return templates.TemplateResponse(
        request, "partials/copilot_confirm_result.html", {"result": result, "kind": "order"}
    )


@html_router.post(
    "/copilot/turns/{turn_id}/confirm-report", response_class=HTMLResponse
)
async def confirm_report(
    turn_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    """Persist a stashed CustomerReportDraft as a real ``customer_reports`` row.

    Reads optional override fields from the form: ``varroa_pct`` (float),
    ``visit_date`` (ISO date), ``narrative`` (free text). When set, these
    override the LLM-extracted values from the stashed draft.

    Side effects: INSERT on customer_reports.
    """
    form = await request.form()
    overrides: dict[str, str] = {
        k: v for k, v in form.items() if isinstance(v, str)
    }
    result = _persist_report_draft(
        turn_id=turn_id, db=db, overrides=overrides,
    )
    return templates.TemplateResponse(
        request, "partials/copilot_confirm_result.html", {"result": result, "kind": "report"}
    )


def _persist_order_draft(
    *,
    turn_id: uuid.UUID,
    db: Session,
    overrides: dict[str, str] | None = None,
) -> ConfirmResultOut:
    """Look up the assistant turn, re-hydrate the OrderDraft, optionally
    apply user-edited overrides, then persist.

    Returns a ``ConfirmResultOut`` ready to be rendered or returned via JSON.
    """
    turn = db.get(ChatTurn, turn_id)
    if turn is None:
        return ConfirmResultOut(
            ok=False, persisted_id=None,
            message=f"Could not find turn {turn_id}.",
        )

    _, pending_order_data, _ = _split_tool_calls(turn.tool_calls)
    if pending_order_data is None:
        return ConfirmResultOut(
            ok=False, persisted_id=None,
            message="No pending order draft on this turn.",
        )

    draft = OrderDraft.model_validate(pending_order_data)
    if draft.customer.customer_id is None:
        return ConfirmResultOut(
            ok=False, persisted_id=None,
            message=(
                "Cannot save: the draft did not match a real customer. "
                "Try rephrasing the original message with a clearer "
                "customer name."
            ),
        )

    if overrides:
        draft = apply_order_overrides(draft, overrides)

    order = persist_order_from_draft(
        draft, db,
        metadata={
            "source": "copilot_confirm",
            "turn_id": str(turn_id),
            "edited_by_user": bool(overrides),
        },
    )
    return ConfirmResultOut(
        ok=True,
        persisted_id=order.id,
        message=(
            f"Saved order for {draft.customer.customer_name} — "
            f"{len(draft.line_items)} items, ${draft.total_usd:,.2f}, "
            f"state {draft.state.value}."
        ),
    )


def _persist_report_draft(
    *,
    turn_id: uuid.UUID,
    db: Session,
    overrides: dict[str, str] | None = None,
) -> ConfirmResultOut:
    """Look up the assistant turn, re-hydrate the CustomerReportDraft, persist.

    Falls back to the conversation's active_account_id when the draft
    doesn't carry a verbatim customer hint, and to today's date when no
    visit_date was extracted. ``hives_placed`` defaults to 0; ``narrative``
    is composed from observations + action items if no other source.
    """
    turn = db.get(ChatTurn, turn_id)
    if turn is None:
        return ConfirmResultOut(
            ok=False, persisted_id=None,
            message=f"Could not find turn {turn_id}.",
        )

    _, _, pending_report_data = _split_tool_calls(turn.tool_calls)
    if pending_report_data is None:
        return ConfirmResultOut(
            ok=False, persisted_id=None,
            message="No pending report draft on this turn.",
        )

    draft = CustomerReportDraft.model_validate(pending_report_data)
    conv = db.get(Conversation, turn.conversation_id)
    if conv is None:
        return ConfirmResultOut(
            ok=False, persisted_id=None,
            message=f"Conversation {turn.conversation_id} disappeared mid-confirm.",
        )

    customer_id = conv.active_account_id
    if customer_id is None and draft.customer_id_hint:
        # Fall back to semantic_matcher when no active account is set on the
        # conversation — covers confirm-report calls on fresh conversations.
        from providers import semantic_matcher
        from agents.schemas import ConversationContext
        match = semantic_matcher.match_customer(
            draft.customer_id_hint,
            conversation_ctx=ConversationContext(),
            conversation_id=turn.conversation_id,
            db=db,
        )
        if match.customer_id is not None:
            customer_id = match.customer_id

    if customer_id is None:
        return ConfirmResultOut(
            ok=False, persisted_id=None,
            message=(
                "Cannot save: no customer was resolved for this report. "
                "Try mentioning the customer name explicitly in the message."
            ),
        )

    user = resolve_default_apiarist(db)
    site = _resolve_active_customer_site(db, customer_id)

    overrides = overrides or {}

    visit_date: date
    raw_visit = overrides.get("visit_date", "").strip()
    if raw_visit:
        try:
            visit_date = date.fromisoformat(raw_visit)
        except ValueError:
            visit_date = (
                draft.visit_date.date()
                if isinstance(draft.visit_date, datetime)
                else (draft.visit_date or date.today())
            )
    elif draft.visit_date is not None:
        visit_date = (
            draft.visit_date.date()
            if isinstance(draft.visit_date, datetime)
            else draft.visit_date
        )
    else:
        visit_date = date.today()

    raw_varroa = overrides.get("varroa_pct", "").strip()
    if raw_varroa:
        try:
            varroa_count = Decimal(raw_varroa)
        except Exception:
            varroa_count = (
                Decimal(str(draft.varroa_pct))
                if draft.varroa_pct is not None
                else Decimal("0")
            )
    elif draft.varroa_pct is not None:
        varroa_count = Decimal(str(draft.varroa_pct))
    else:
        varroa_count = Decimal("0")

    raw_narrative = overrides.get("narrative", "").strip()
    if raw_narrative:
        narrative = raw_narrative
    else:
        narrative_parts: list[str] = []
        if draft.observations:
            narrative_parts.append("Observations: " + "; ".join(draft.observations))
        if draft.action_items:
            narrative_parts.append("Follow-ups: " + "; ".join(draft.action_items))
        narrative = "\n".join(narrative_parts) or "(no narrative captured)"

    tags = ["from_copilot"]
    if any(overrides.get(k, "").strip() for k in ("varroa_pct", "visit_date", "narrative")):
        tags.append("edited_by_user")
    report = CustomerReport(
        customer_site_id=site.id,
        author_id=user.id,
        visit_date=visit_date,
        hives_placed=0,
        varroa_count=varroa_count,
        diseases_observed=[],
        pesticide_exposure_note=None,
        narrative=narrative,
        tags=tags,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return ConfirmResultOut(
        ok=True,
        persisted_id=report.id,
        message=(
            f"Saved customer report for {site.name} on "
            f"{visit_date.isoformat()} (varroa {varroa_count})."
        ),
    )


# ---------------------------------------------------------------------------
# HTMX action: confirm-match — resume a paused turn after customer selection
# ---------------------------------------------------------------------------


@html_router.post(
    "/copilot/turns/{turn_id}/confirm-match", response_class=HTMLResponse
)
async def confirm_match(
    request: Request,
    turn_id: uuid.UUID,
    customer_id: uuid.UUID | None = Form(default=None),
    reject: bool = Query(default=False),
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    """Resume a paused turn whose matcher returned low confidence.

    Two paths:
      * ``customer_id`` provided: commit that customer, re-dispatch via
        ``resume_with_customer``, return the resumed assistant turn partial.
      * ``reject=1``: swap in the pick-match card listing the alternatives
        stored in the ``pending_customer_match`` marker on the turn.

    Raises ``HTTPException`` 404 when the turn is missing, 409 when the
    turn has no pending match marker, and 400 when ``customer_id`` is
    absent on the non-reject path.

    Side effects: on the commit path, one INSERT on chat_turns and one
    UPDATE on the paused turn's tool_calls column.
    """
    turn = db.get(ChatTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Turn not found")

    pending = _extract_pending_match(turn)
    if pending is None:
        raise HTTPException(
            status_code=409, detail="No pending match on this turn"
        )

    if reject:
        alternatives = [
            {
                "customer_id": c[0],
                "customer_name": c[1],
                "confidence": c[2],
            }
            for c in pending.get("alternatives", [])
        ]
        return templates.TemplateResponse(
            request,
            "partials/copilot_pick_match.html",
            {"turn_id": turn_id, "alternatives": alternatives},
        )

    if customer_id is None:
        raise HTTPException(
            status_code=400, detail="customer_id required when reject is not set"
        )

    from agents.copilot.orchestrator import resume_with_customer

    resumed_turn = resume_with_customer(
        conv_id=_coerce_uuid(turn.conversation_id),
        paused_turn=turn,
        chosen_customer_id=customer_id,
        db=db,
    )
    _clear_pending_match(turn, db)

    resumed_view = _turn_view_for_template(resumed_turn)
    return templates.TemplateResponse(
        request,
        "partials/copilot_turn_assistant.html",
        {"turn": resumed_view},
    )


def _extract_pending_match(turn: ChatTurn) -> dict | None:
    """Return the first ``pending_customer_match`` record from a turn's tool_calls.

    Returns ``None`` if no such record exists.
    """
    for tc in turn.tool_calls or []:
        if tc.get("tool_name") == "pending_customer_match":
            return tc
    return None


def _clear_pending_match(turn: ChatTurn, db: Session) -> None:
    """Remove the ``pending_customer_match`` record from a turn's tool_calls.

    Side effects: mutates ``turn.tool_calls`` in place and commits.
    """
    turn.tool_calls = [
        tc
        for tc in (turn.tool_calls or [])
        if tc.get("tool_name") != "pending_customer_match"
    ]
    db.add(turn)
    db.commit()


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@api_router.get("/conversations", response_model=ConversationListOut)
def get_conversations(
    db: Session = Depends(get_db_session),
    limit: int = 50,
) -> ConversationListOut:
    """List conversations, newest-first."""
    items = _list_conversations(db, limit=limit)
    total = db.execute(select(func.count(Conversation.id))).scalar_one()
    return ConversationListOut(items=items, total=int(total))


@api_router.get(
    "/conversations/{conversation_id}", response_model=ConversationOut
)
def get_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db_session),
) -> ConversationOut:
    """Read one conversation with all of its turns hydrated."""
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    turns = _load_turns(db, conversation_id)
    active_account_name: str | None = None
    if conv.active_account_id:
        cust = db.get(Customer, conv.active_account_id)
        if cust is not None:
            active_account_name = cust.name
    return ConversationOut(
        id=conv.id,
        salesperson_id=conv.salesperson_id,
        active_account_id=conv.active_account_id,
        active_account_name=active_account_name,
        title=conv.title,
        model_used=conv.model_used,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        turns=[_turn_to_out(t) for t in turns],
    )


@api_router.post(
    "/conversations", response_model=ConversationOut, status_code=201
)
def post_create_conversation(
    body: ConversationCreateIn,
    db: Session = Depends(get_db_session),
) -> ConversationOut:
    """Programmatic create-conversation endpoint (mirrors the HTMX one)."""
    conv = Conversation(active_account_id=body.active_account_id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    active_account_name: str | None = None
    if conv.active_account_id:
        cust = db.get(Customer, conv.active_account_id)
        if cust is not None:
            active_account_name = cust.name
    return ConversationOut(
        id=conv.id,
        salesperson_id=conv.salesperson_id,
        active_account_id=conv.active_account_id,
        active_account_name=active_account_name,
        title=conv.title,
        model_used=conv.model_used,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        turns=[],
    )


@api_router.post(
    "/turns/{turn_id}/confirm-order", response_model=ConfirmResultOut
)
def api_confirm_order(
    turn_id: uuid.UUID,
    db: Session = Depends(get_db_session),
) -> ConfirmResultOut:
    """JSON variant of the HTMX confirm-order endpoint."""
    return _persist_order_draft(turn_id=turn_id, db=db)


@api_router.post(
    "/turns/{turn_id}/confirm-report", response_model=ConfirmResultOut
)
def api_confirm_report(
    turn_id: uuid.UUID,
    db: Session = Depends(get_db_session),
) -> ConfirmResultOut:
    """JSON variant of the HTMX confirm-report endpoint."""
    return _persist_report_draft(turn_id=turn_id, db=db)


__all__ = [
    "html_router",
    "api_router",
]
