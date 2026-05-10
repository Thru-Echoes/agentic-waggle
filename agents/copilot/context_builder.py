"""Build a ContextBundle for one copilot chat turn.

Pulls:
  - the customer's name + erp_no (when ``customer_id`` is known),
  - their N most recent customer reports,
  - top-K semantic-search chunks via the existing
    ``providers.vector_search.VectorSearchProvider``.

Token-budget aware: trims chunks / report excerpts so the assembled
context fits inside ``token_budget``. Token counts are estimated by
the rough 1 token ≈ 4 chars heuristic — good enough for the budget
gate; precise counting happens at the LLM call site if needed.

Side effects:
  - a few SELECTs against the provided session,
  - one sentence-transformer embedding call (via the provider),
  - one pgvector cosine query.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from providers.vector_search import VectorSearchProvider, SearchResult


# Default budget for a single chat-turn context (input side only). Picked
# generously — the full LLM context window is 128K-272K depending on
# model; we leave most of it for conversation history and the actual
# response. 6K input tokens is plenty for one turn's grounding.
DEFAULT_TOKEN_BUDGET = 6_000

# How many customer reports to surface for a known customer.
DEFAULT_REPORT_LIMIT = 3


def _estimate_tokens(s: str) -> int:
    """Rough token count for budget gating. 1 token ≈ 4 chars (English-ish)."""
    return max(1, (len(s) + 3) // 4)


@dataclass(frozen=True)
class ReportSummary:
    """One customer report rendered down to its essentials for prompt context."""

    visit_date: str          # ISO date string
    site_name: str | None
    excerpt: str             # first ~500 chars of narrative
    varroa_count: int | None


@dataclass(frozen=True)
class ContextBundle:
    """Everything the copilot needs to ground one chat-turn response.

    Confidence + per-step metrics live on the surrounding ChatTurn —
    this bundle is purely the read-side context.
    """

    customer_name: str | None = None
    erp_customer_no: str | None = None
    recent_reports: list[ReportSummary] = field(default_factory=list)
    rag_chunks: list[str] = field(default_factory=list)
    estimated_tokens: int = 0


def _load_customer(
    db: Session, customer_id: uuid.UUID | None
) -> tuple[str | None, str | None]:
    """Return (customer_name, erp_customer_no) or (None, None)."""
    if customer_id is None:
        return None, None
    row = db.execute(
        text(
            "SELECT name, erp_customer_no FROM customers WHERE id = :cid"
        ),
        {"cid": str(customer_id)},
    ).mappings().first()
    if row is None:
        return None, None
    return row["name"], row["erp_customer_no"]


def _load_recent_reports(
    db: Session,
    customer_id: uuid.UUID,
    limit: int,
) -> list[ReportSummary]:
    """Pull the N most recent customer reports for a given customer."""
    rows = db.execute(
        text(
            """
            SELECT cr.visit_date, cs.name AS site_name,
                   cr.narrative, cr.varroa_count
            FROM customer_reports cr
            JOIN customer_sites cs ON cs.id = cr.customer_site_id
            WHERE cs.customer_id = :cid
            ORDER BY cr.visit_date DESC
            LIMIT :lim
            """
        ),
        {"cid": str(customer_id), "lim": limit},
    ).mappings().all()
    out: list[ReportSummary] = []
    for r in rows:
        narrative = r.get("narrative") or ""
        excerpt = narrative[:500]
        out.append(
            ReportSummary(
                visit_date=str(r["visit_date"]),
                site_name=r.get("site_name"),
                excerpt=excerpt,
                varroa_count=r.get("varroa_count"),
            )
        )
    return out


def _rag_chunks(
    query: str,
    db: Session,
    top_k: int,
    customer_id: uuid.UUID | None,
) -> list[str]:
    """Cosine search via the existing VectorSearchProvider.

    When a customer is known, we filter by their site (if any). When
    unknown, we let the search range over everything.
    """
    provider = VectorSearchProvider(db=db)
    filters: dict[str, Any] = {}
    if customer_id is not None:
        # Best-effort site filter: pick any one of the customer's sites
        # to constrain the search. If the customer has many sites or the
        # filter shape doesn't apply cleanly, fall through unfiltered.
        site_row = db.execute(
            text(
                "SELECT id FROM customer_sites WHERE customer_id = :cid LIMIT 1"
            ),
            {"cid": str(customer_id)},
        ).mappings().first()
        if site_row is not None:
            filters["customer_site_id"] = str(site_row["id"])
    results: list[SearchResult] = provider.search(
        query=query, top_k=top_k, filters=filters or None
    )
    return [r.content for r in results]


def _trim_to_budget(
    bundle_parts: dict[str, Any], budget: int
) -> tuple[list[ReportSummary], list[str], int]:
    """Greedy trim — drop excerpts / chunks until the estimated token
    count fits. Customer header is mandatory and always included."""
    header_tokens = _estimate_tokens(
        f"{bundle_parts['customer_name'] or ''} {bundle_parts['erp_customer_no'] or ''}"
    )
    reports: list[ReportSummary] = list(bundle_parts["recent_reports"])
    chunks: list[str] = list(bundle_parts["rag_chunks"])

    def _total() -> int:
        t = header_tokens
        for rep in reports:
            t += _estimate_tokens(rep.excerpt) + 16  # 16 for date/site/varroa
        for c in chunks:
            t += _estimate_tokens(c)
        return t

    # Drop reports first (newest-last is fine: they're already sorted
    # newest-first, so pop oldest from the end).
    while _total() > budget and reports:
        reports.pop()
    # Then drop RAG chunks (lowest-relevance last).
    while _total() > budget and chunks:
        chunks.pop()

    return reports, chunks, _total()


def build_context(
    *,
    query: str,
    customer_id: uuid.UUID | None,
    db: Session,
    top_k: int = 5,
    report_limit: int = DEFAULT_REPORT_LIMIT,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> ContextBundle:
    """Assemble a ContextBundle for one chat turn.

    Side effects: SELECTs against ``db``; one embedding call; one pgvector
    cosine query.
    """
    customer_name, erp_no = _load_customer(db, customer_id)
    recent_reports: list[ReportSummary] = []
    if customer_id is not None:
        recent_reports = _load_recent_reports(db, customer_id, report_limit)
    chunks = _rag_chunks(query, db, top_k, customer_id)

    parts = {
        "customer_name": customer_name,
        "erp_customer_no": erp_no,
        "recent_reports": recent_reports,
        "rag_chunks": chunks,
    }
    trimmed_reports, trimmed_chunks, est_tokens = _trim_to_budget(parts, token_budget)
    return ContextBundle(
        customer_name=customer_name,
        erp_customer_no=erp_no,
        recent_reports=trimmed_reports,
        rag_chunks=trimmed_chunks,
        estimated_tokens=est_tokens,
    )
