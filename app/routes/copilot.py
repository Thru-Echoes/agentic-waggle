"""Sales-copilot routes (HTML + HTMX + JSON API).

Surfaces in this module:

    GET  /copilot                                 — chat shell, auto-selects newest conv
    GET  /copilot/{conversation_id}               — chat shell, anchored on a specific conv
    POST /copilot/turns                           — create user turn → orchestrator → render assistant
    POST /copilot/conversations                   — new empty conversation, redirect
    POST /copilot/turns/{turn_id}/confirm-order   — persist a stashed OrderDraft → orders row
    POST /copilot/turns/{turn_id}/confirm-report  — persist a stashed CustomerReportDraft
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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select, text
from sqlmodel import Session

from agents.copilot.account_resolver import AccountIndex, build_account_index
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


def _build_account_index_safe(db: Session) -> AccountIndex | None:
    """Build the account index; return None if customers table is empty.

    The orchestrator handles ``account_index=None`` by skipping the
    deterministic resolver — this keeps fresh-DB demos working before any
    customers are seeded.
    """
    count = db.execute(select(func.count(Customer.id))).scalar_one()
    if count == 0:
        return None
    return build_account_index(db)


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
    conv: Conversation, first_user_text: str, db: Session
) -> None:
    """Set Conversation.title on the first user turn if not already set.

    Falls back to the first ~60 chars of the user's text. We keep this
    deterministic to avoid an extra LLM call per conversation in the
    demo path; the LLM-titled variant is a future polish.

    Side effects: UPDATE on ``conversations`` if title is set.
    """
    if conv.title:
        return
    title = first_user_text.strip().splitlines()[0] if first_user_text.strip() else ""
    if not title:
        title = "Untitled conversation"
    if len(title) > _TITLE_FALLBACK_LIMIT:
        title = title[:_TITLE_FALLBACK_LIMIT].rstrip() + "…"
    conv.title = title
    db.add(conv)
    db.commit()


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


@html_router.post("/copilot/turns", response_class=HTMLResponse)
def post_turn(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    conversation_id: uuid.UUID = Form(...),
    text: str = Form(..., min_length=1, max_length=8_000),
) -> HTMLResponse:
    """Create a user turn → run orchestrator → persist + return both partials.

    Side effects:
      * INSERT user ChatTurn
      * 1-3 LLM calls + DB SELECTs via the orchestrator
      * INSERT assistant ChatTurn
      * UPDATE Conversation.updated_at + active_account_id (and title on
        first turn)
    """
    conv = _get_or_create_conversation(db, conversation_id)

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
    _maybe_title_conversation(conv, text, db)

    # 3. Run the orchestrator.
    models = _build_models(settings)
    account_index = _build_account_index_safe(db)
    response = get_copilot_response(
        user_text=text,
        db=db,
        models=models,
        account_index=account_index,
        active_account_id=conv.active_account_id,
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
def confirm_order(
    turn_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    """Persist a stashed OrderDraft as a real ``orders`` row + line items.

    Side effects: INSERT on orders, order_line_items, order_state_transitions.
    """
    result = _persist_order_draft(turn_id=turn_id, db=db)
    return templates.TemplateResponse(
        request, "partials/copilot_confirm_result.html", {"result": result, "kind": "order"}
    )


@html_router.post(
    "/copilot/turns/{turn_id}/confirm-report", response_class=HTMLResponse
)
def confirm_report(
    turn_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    """Persist a stashed CustomerReportDraft as a real ``customer_reports`` row.

    Side effects: INSERT on customer_reports.
    """
    result = _persist_report_draft(turn_id=turn_id, db=db)
    return templates.TemplateResponse(
        request, "partials/copilot_confirm_result.html", {"result": result, "kind": "report"}
    )


def _persist_order_draft(*, turn_id: uuid.UUID, db: Session) -> ConfirmResultOut:
    """Look up the assistant turn, re-hydrate the OrderDraft, persist.

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

    order = persist_order_from_draft(
        draft, db,
        metadata={"source": "copilot_confirm", "turn_id": str(turn_id)},
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


def _persist_report_draft(*, turn_id: uuid.UUID, db: Session) -> ConfirmResultOut:
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
        # Fall back to a fresh deterministic resolver call against the hint.
        index = _build_account_index_safe(db)
        if index is not None:
            from agents.copilot.account_resolver import resolve_account
            ids = resolve_account(draft.customer_id_hint, index)
            if ids:
                customer_id = ids[0]

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

    visit_date: date
    if draft.visit_date is not None:
        visit_date = draft.visit_date.date() if isinstance(draft.visit_date, datetime) else draft.visit_date
    else:
        visit_date = date.today()

    varroa_count = (
        Decimal(str(draft.varroa_pct))
        if draft.varroa_pct is not None
        else Decimal("0")
    )

    narrative_parts: list[str] = []
    if draft.observations:
        narrative_parts.append("Observations: " + "; ".join(draft.observations))
    if draft.action_items:
        narrative_parts.append("Follow-ups: " + "; ".join(draft.action_items))
    narrative = "\n".join(narrative_parts) or "(no narrative captured)"

    report = CustomerReport(
        customer_site_id=site.id,
        author_id=user.id,
        visit_date=visit_date,
        hives_placed=0,
        varroa_count=varroa_count,
        diseases_observed=[],
        pesticide_exposure_note=None,
        narrative=narrative,
        tags=["from_copilot"],
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
