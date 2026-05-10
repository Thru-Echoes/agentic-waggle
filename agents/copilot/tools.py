"""Copilot tool implementations.

The orchestrator dispatches each chat turn's intent to one of four tools:

    lookup_customer            — quick DB lookup by name / id (no LLM)
    search_customer_reports    — vector search filtered by customer / topic
    draft_order_from_text      — reuses Phase A's order extractor
    log_customer_report        — LLM-extracts a structured report from
                                 free-text or transcribed dictation

Each returns a Pydantic-shaped result the orchestrator wraps in a
``ToolCallRecord`` for the chat-turn log.

Side effects: SELECTs against the provided session; one or zero LLM
calls per tool.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from langchain_core.language_models import BaseChatModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.copilot.conversation_schemas import CustomerReportDraft
from agents.schemas import EmailInput

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool 1: lookup_customer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CustomerLookup:
    """Quick public-shape view of a single customer."""

    id: uuid.UUID
    name: str
    erp_customer_no: str
    aliases: list[str]
    site_count: int


def lookup_customer(
    db: Session, *, customer_id: uuid.UUID
) -> CustomerLookup | None:
    """Fetch the public-facing fields for a single customer.

    Returns ``None`` if the id doesn't exist (orchestrator surfaces this
    as a calm "I don't see that customer" message).

    Side effects: 2 SELECTs (customer row + site count).
    """
    row = db.execute(
        text(
            "SELECT id, name, erp_customer_no, aliases "
            "FROM customers WHERE id = :cid"
        ),
        {"cid": str(customer_id)},
    ).mappings().first()
    if row is None:
        return None
    site_count = db.execute(
        text("SELECT COUNT(*) FROM customer_sites WHERE customer_id = :cid"),
        {"cid": str(customer_id)},
    ).scalar_one()
    cid = row["id"]
    if isinstance(cid, str):
        cid = uuid.UUID(cid)
    return CustomerLookup(
        id=cid,
        name=row["name"],
        erp_customer_no=row["erp_customer_no"],
        aliases=list(row["aliases"] or []),
        site_count=int(site_count),
    )


# ---------------------------------------------------------------------------
# Tool 2: search_customer_reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportHit:
    """One customer-report hit from the vector search."""

    report_id: uuid.UUID
    visit_date: str
    site_name: str | None
    excerpt: str
    score: float


def search_customer_reports(
    db: Session,
    *,
    query: str,
    customer_id: uuid.UUID | None = None,
    top_k: int = 5,
) -> list[ReportHit]:
    """Hybrid (semantic + filter) search over customer reports.

    Falls back to plain SQL when ``query`` is empty (just returns the
    most recent reports for the customer).

    Side effects: one embedding call + one cosine query when ``query``
    is non-empty; otherwise one SQL select.
    """
    if not query.strip() and customer_id is not None:
        rows = db.execute(
            text(
                """
                SELECT cr.id, cr.visit_date, cs.name AS site_name,
                       cr.narrative
                FROM customer_reports cr
                JOIN customer_sites cs ON cs.id = cr.customer_site_id
                WHERE cs.customer_id = :cid
                ORDER BY cr.visit_date DESC
                LIMIT :lim
                """
            ),
            {"cid": str(customer_id), "lim": top_k},
        ).mappings().all()
        out: list[ReportHit] = []
        for r in rows:
            rid = r["id"]
            if isinstance(rid, str):
                rid = uuid.UUID(rid)
            out.append(
                ReportHit(
                    report_id=rid,
                    visit_date=str(r["visit_date"]),
                    site_name=r.get("site_name"),
                    excerpt=(r.get("narrative") or "")[:400],
                    score=1.0,
                )
            )
        return out

    from providers.vector_search import VectorSearchProvider

    provider = VectorSearchProvider(db=db)
    filters: dict[str, Any] = {"source_type": "customer_report"}
    if customer_id is not None:
        # Best-effort: pin to one of the customer's sites.
        site_row = db.execute(
            text(
                "SELECT id FROM customer_sites WHERE customer_id = :cid LIMIT 1"
            ),
            {"cid": str(customer_id)},
        ).mappings().first()
        if site_row is not None:
            filters["customer_site_id"] = str(site_row["id"])
    results = provider.search(query=query, top_k=top_k, filters=filters)
    out2: list[ReportHit] = []
    for r in results:
        meta = r.metadata or {}
        report_id = meta.get("customer_report_id") or meta.get("report_id")
        if not report_id:
            continue
        if isinstance(report_id, str):
            try:
                report_id = uuid.UUID(report_id)
            except ValueError:
                continue
        out2.append(
            ReportHit(
                report_id=report_id,
                visit_date=str(meta.get("visit_date") or ""),
                site_name=meta.get("site_name"),
                excerpt=r.content[:400],
                score=float(r.score),
            )
        )
    return out2


# ---------------------------------------------------------------------------
# Tool 3: draft_order_from_text
# ---------------------------------------------------------------------------


def draft_order_from_text(
    db: Session,
    *,
    text_in: str,
    from_address: str = "copilot@waggle.local",
    subject: str = "Order draft from copilot",
):
    """Run Phase A's order extractor against free-text input.

    Wraps the input in an ``EmailInput`` shape so the existing pipeline
    handles it without modification. Returns ``OrderDraft`` or
    ``ExtractionFailure``.

    Side effects: full extraction pipeline (3-5 LLM calls + DB lookups).
    """
    from agents.order_extractor import run_order_extraction

    email = EmailInput(
        external_id=None,
        from_address=from_address,
        subject=subject,
        body_text=text_in,
    )
    return run_order_extraction(email, db)


# ---------------------------------------------------------------------------
# Tool 4: log_customer_report
# ---------------------------------------------------------------------------


_REPORT_PROMPT = """\
Extract a structured customer-visit report from this dictated or typed input.

Input:
{text}

Today's date is {today}. Use it to resolve relative dates ("yesterday",
"last week"), but only set ``visit_date`` if the input gives an explicit
or clearly-implied date. Otherwise leave it null.

Fields to fill:
- visit_date: ISO date or null
- customer_id_hint: the customer / company name verbatim if mentioned, else null
- varroa_pct: a varroa-mite percentage if the input states one
- observations: short bullet observations from the visit
- action_items: concrete next steps the rep said they'd take
- evidence: 1-2 verbatim quotes from the input justifying the extraction
  (raw text, no surrounding quotation marks)
- confidence: 0.0-1.0 self-rated
"""


def log_customer_report(
    *,
    text_in: str,
    model: BaseChatModel,
) -> CustomerReportDraft:
    """LLM-extract a structured customer report from free-text.

    The orchestrator surfaces the draft to the user for confirmation
    before persisting to the ``customer_reports`` table — this tool
    only does the extraction.

    Side effects: one LLM call.
    """
    structured = model.with_structured_output(CustomerReportDraft)
    prompt = _REPORT_PROMPT.format(
        text=text_in, today=date.today().isoformat()
    )
    result: CustomerReportDraft = structured.invoke(prompt)  # type: ignore[assignment]
    return result


__all__ = [
    "CustomerLookup",
    "ReportHit",
    "lookup_customer",
    "search_customer_reports",
    "draft_order_from_text",
    "log_customer_report",
]
