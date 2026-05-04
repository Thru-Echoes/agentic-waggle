"""Server-rendered HTML routes (Jinja + HTMX).

These are distinct from the JSON API routes — the API returns `OrderOut`,
while the page route returns rendered HTML that includes the same data
plus Waggle layout. HTMX partials are returned as fragments so the page
can update in place without a full re-render.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlmodel import Session

from agents.order_extractor import run_order_extraction
from agents.schemas import EmailInput, ExtractionFailure, OrderDraft
from app.config import Settings, get_settings
from db.enums import OrderState
from db.models import Customer, Order, CustomerReport
from db.session import get_db_session
from providers.inbox import FIXTURE_ROOT, InboxClient, InboxFixtureMissing

router = APIRouter(tags=["pages"], include_in_schema=False)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

ERP_LOG_ROOT = Path("/erp-sync-log")


def _common_context(request: Request, settings: Settings) -> dict:
    """Vars every template expects: product name, tagline, current nav item."""
    return {
        "request": request,
        "product": settings.product_name,
        "tagline": settings.tagline,
        "company": settings.company_name,
    }


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Dashboard — summary tiles + recent orders + recent customer reports."""
    customer_count = db.execute(select(func.count(Customer.id))).scalar_one()
    order_count = db.execute(select(func.count(Order.id))).scalar_one()
    customer_report_count = db.execute(select(func.count(CustomerReport.id))).scalar_one()

    by_state = dict(
        db.execute(
            select(Order.state, func.count(Order.id)).group_by(Order.state)
        ).all()
    )
    state_summary = [
        {"state": s.value, "count": by_state.get(s, 0)} for s in OrderState
    ]

    recent_orders = db.execute(
        select(Order)
        .options(selectinload(Order.customer), selectinload(Order.apiarist))
        .order_by(Order.created_at.desc())  # type: ignore[attr-defined]
        .limit(8)
    ).scalars().all()

    recent_customer_reports = db.execute(
        select(CustomerReport)
        .options(selectinload(CustomerReport.site), selectinload(CustomerReport.author))
        .order_by(CustomerReport.visit_date.desc())  # type: ignore[attr-defined]
        .limit(5)
    ).scalars().all()

    ctx = _common_context(request, settings) | {
        "nav": "dashboard",
        "customer_count": customer_count,
        "order_count": order_count,
        "customer_report_count": customer_report_count,
        "state_summary": state_summary,
        "recent_orders": recent_orders,
        "recent_customer_reports": recent_customer_reports,
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get("/customers", response_class=HTMLResponse)
def customers_page(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    rows = db.execute(
        select(Customer).order_by(Customer.name).limit(200)
    ).scalars().all()
    ctx = _common_context(request, settings) | {
        "nav": "customers",
        "customers": rows,
    }
    return templates.TemplateResponse(request, "customers.html", ctx)


@router.get("/orders", response_class=HTMLResponse)
def orders_page(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    state: OrderState | None = None,
) -> HTMLResponse:
    q = select(Order).options(
        selectinload(Order.customer), selectinload(Order.apiarist)
    )
    if state is not None:
        q = q.where(Order.state == state)
    rows = db.execute(
        q.order_by(Order.created_at.desc()).limit(100)  # type: ignore[attr-defined]
    ).scalars().all()
    ctx = _common_context(request, settings) | {
        "nav": "orders",
        "orders": rows,
        "states": list(OrderState),
        "current_state": state,
    }
    return templates.TemplateResponse(request, "orders.html", ctx)


@router.get("/customer-reports", response_class=HTMLResponse)
def customer_reports_page(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    rows = db.execute(
        select(CustomerReport)
        .options(
            selectinload(CustomerReport.site), selectinload(CustomerReport.author)
        )
        .order_by(CustomerReport.visit_date.desc())  # type: ignore[attr-defined]
        .limit(50)
    ).scalars().all()
    ctx = _common_context(request, settings) | {
        "nav": "customer_reports",
        "customer_reports": rows,
    }
    return templates.TemplateResponse(request, "customer_reports.html", ctx)


@router.get("/customer-reports/{customer_report_id}/partial", response_class=HTMLResponse)
def customer_report_partial(
    customer_report_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX partial — detail pane swapped inline when a row is clicked."""
    row = db.execute(
        select(CustomerReport)
        .where(CustomerReport.id == customer_report_id)
        .options(
            selectinload(CustomerReport.site), selectinload(CustomerReport.author)
        )
    ).scalar_one_or_none()
    if row is None:
        return HTMLResponse("<div class='empty'>Not found</div>", status_code=404)
    return templates.TemplateResponse(
        request,
        "partials/customer_report_detail.html",
        {"customer_report": row},
    )


# ---------------------------------------------------------------------------
# Inbox + agent extraction
# ---------------------------------------------------------------------------


def _list_all_inbox_emails() -> list[dict[str, Any]]:
    """Read every `*.inbox.json` fixture and return a flat, newest-first list.

    Side effect: filesystem reads from `FIXTURE_ROOT`. Raises
    `InboxFixtureMissing` if the fixture root does not exist (no inbox to
    show would mask a seed-pipeline bug — see fail-loud.md).
    """
    if not FIXTURE_ROOT.exists():
        raise InboxFixtureMissing(
            f"Inbox fixture root {FIXTURE_ROOT} does not exist. "
            f"Run `make seed` to populate it."
        )
    rows: list[dict[str, Any]] = []
    for fpath in sorted(FIXTURE_ROOT.glob("*.inbox.json")):
        rows.extend(json.loads(fpath.read_text()))
    rows.sort(key=lambda r: r["received_at"], reverse=True)
    return rows


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Inbound mail surfaced from the gmail-fixtures provider.

    Each row links to an HTMX `POST /inbox/extract` button that runs the
    LangGraph agent and swaps the resulting OrderDraft (or failure) into a
    detail pane.
    """
    try:
        emails = _list_all_inbox_emails()
    except InboxFixtureMissing as exc:
        ctx = _common_context(request, settings) | {
            "nav": "inbox",
            "emails": [],
            "fixture_error": str(exc),
        }
        return templates.TemplateResponse(request, "inbox.html", ctx)

    ctx = _common_context(request, settings) | {
        "nav": "inbox",
        "emails": emails,
        "fixture_error": None,
    }
    return templates.TemplateResponse(request, "inbox.html", ctx)


@router.post("/inbox/extract", response_class=HTMLResponse)
def inbox_extract(
    request: Request,
    account_id: str = Form(...),
    message_id: str = Form(...),
    db: Session = Depends(get_db_session),
) -> HTMLResponse:
    """HTMX action — fetch one fixture email and run the LangGraph agent.

    Returns a Jinja partial: either `agent_draft.html` for an OrderDraft or
    `agent_failure.html` for an ExtractionFailure. Side effects: one or more
    LLM calls, no DB writes (the draft is not persisted; that's still
    Phase E).
    """
    inbox = InboxClient()
    msg = inbox.fetch(account_id, message_id)
    email_input = EmailInput(
        external_id=msg.id,
        from_address=msg.from_address,
        subject=msg.subject,
        body_text=msg.body_text,
    )
    result = run_order_extraction(email_input, db)
    if isinstance(result, OrderDraft):
        return templates.TemplateResponse(
            request,
            "partials/agent_draft.html",
            {"draft": result, "email": msg},
        )
    assert isinstance(result, ExtractionFailure)
    return templates.TemplateResponse(
        request,
        "partials/agent_failure.html",
        {"failure": result, "email": msg},
    )


# ---------------------------------------------------------------------------
# ERP sync log
# ---------------------------------------------------------------------------


def _read_erp_events(limit: int = 200) -> list[dict[str, Any]]:
    """Read the JSON-line log files and return events newest-first.

    Side effect: filesystem reads from `ERP_LOG_ROOT`.
    """
    events: list[dict[str, Any]] = []
    if not ERP_LOG_ROOT.exists():
        return events
    for fpath in sorted(ERP_LOG_ROOT.glob("*.jsonl")):
        with fpath.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))
    events.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return events[:limit]


@router.get("/erp-log", response_class=HTMLResponse)
def erp_log_page(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Browse the local ERP sync log (JSON-line files in /erp-sync-log)."""
    events = _read_erp_events()
    ctx = _common_context(request, settings) | {
        "nav": "erp_log",
        "events": events,
    }
    return templates.TemplateResponse(request, "erp_log.html", ctx)
