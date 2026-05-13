"""Waggle — Sales Copilot MCP server.

Exposes tools the LLM client uses to answer sales-team questions about
customers, their orders, their customer-report history, and to draft follow-up
emails. All tools open a short-lived DB session per call and fail loudly
on missing data.

Run stdio transport:
    python -m mcp_servers.sales_copilot_server

Run SSE (for browser-based MCP clients):
    python -m mcp_servers.sales_copilot_server --sse
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import text

from db.session import session_scope
from mcp_servers.schemas import (
    CustomerHit,
    DraftedEmail,
    OrderSummary,
    SiteSummary,
    CustomerReportSnippet,
)
from providers.llm import LLMClient
from providers.vector_search import VectorSearchProvider

log = logging.getLogger(__name__)

server: FastMCP = FastMCP(
    "waggle-sales-copilot",
    instructions=(
        "Sales Copilot for Waggle, a beekeeping CRM. Use these tools to "
        "answer questions about commercial customers (orchards, honey "
        "wholesalers, mead makers), their pollination contracts, their "
        "apiary inspection history, and to draft follow-up emails."
    ),
)


@server.tool
def find_customer(name_or_alias: str, top_k: int = 5) -> list[CustomerHit]:
    """Semantic-match a customer by name or alias.

    Use when the user refers to a customer by a partial, misspelled, or
    alias form. Uses embedding + LLM disambiguation. Returns the top-k
    best-matching customers. Returns an empty list if no confident match
    is found.
    """
    import uuid as _uuid
    from agents.schemas import ConversationContext
    from providers import semantic_matcher

    with session_scope() as db:
        match = semantic_matcher.match_customer(
            name_or_alias,
            conversation_ctx=ConversationContext(),
            conversation_id=_uuid.uuid4(),
            db=db,
        )
        if match.customer_id is None:
            return []

        # Collect candidates: the top match plus alternatives, up to top_k.
        candidate_ids: list[uuid.UUID] = [match.customer_id]
        for alt in match.alternatives[: top_k - 1]:
            candidate_ids.append(alt.customer_id)
        candidate_ids = candidate_ids[:top_k]

        rows = (
            db.execute(
                text(
                    "SELECT id, name, erp_customer_no, market_segment "
                    "FROM customers WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [str(cid) for cid in candidate_ids]},
            )
            .mappings()
            .all()
        )
        by_id = {str(r["id"]): r for r in rows}

        # Build ordered output: top match first, then alternatives.
        out: list[CustomerHit] = []
        for i, cid in enumerate(candidate_ids):
            r = by_id.get(str(cid))
            if r is None:
                continue
            # Top match uses the matcher confidence; alternatives use their own.
            if i == 0:
                score = round(match.confidence * 100.0, 1)
            else:
                alt_conf = next(
                    (a.confidence for a in match.alternatives if a.customer_id == cid),
                    0.0,
                )
                score = round(alt_conf * 100.0, 1)
            out.append(
                CustomerHit(
                    customer_id=r["id"],
                    name=r["name"],
                    erp_customer_no=r["erp_customer_no"],
                    market_segment=r["market_segment"],
                    score=score,
                )
            )
        return out


@server.tool
def list_customer_sites(customer_id: str) -> list[SiteSummary]:
    """Return every site belonging to a customer. Raises if the customer doesn't exist."""
    with session_scope() as db:
        cust = db.execute(
            text("SELECT id FROM customers WHERE id = :id"),
            {"id": customer_id},
        ).first()
        if cust is None:
            raise LookupError(f"No customer with id={customer_id!r}.")
        rows = (
            db.execute(
                text(
                    "SELECT id, customer_id, name, acreage_by_crop "
                    "FROM customer_sites WHERE customer_id = :cid "
                    "ORDER BY name"
                ),
                {"cid": customer_id},
            )
            .mappings()
            .all()
        )
        return [
            SiteSummary(
                site_id=r["id"],
                customer_id=r["customer_id"],
                name=r["name"],
                acreage_by_crop=r["acreage_by_crop"] or {},
            )
            for r in rows
        ]


@server.tool
def list_recent_orders(
    customer_id: str,
    limit: int = 10,
) -> list[OrderSummary]:
    """Return the customer's most-recent orders, newest first. Raises if the customer doesn't exist."""
    with session_scope() as db:
        cust = db.execute(
            text("SELECT name FROM customers WHERE id = :id"),
            {"id": customer_id},
        ).first()
        if cust is None:
            raise LookupError(f"No customer with id={customer_id!r}.")
        customer_name = cust[0]
        rows = (
            db.execute(
                text(
                    "SELECT o.id, o.customer_id, u.name AS apiarist_name, "
                    "       o.state, o.total_usd, o.pollination_date, "
                    "       o.created_at "
                    "FROM orders o JOIN users u ON o.user_id = u.id "
                    "WHERE o.customer_id = :cid "
                    "ORDER BY o.created_at DESC "
                    "LIMIT :lim"
                ),
                {"cid": customer_id, "lim": limit},
            )
            .mappings()
            .all()
        )
        return [
            OrderSummary(
                order_id=r["id"],
                customer_id=r["customer_id"],
                customer_name=customer_name,
                apiarist_name=r["apiarist_name"],
                state=r["state"],
                total_usd=float(r["total_usd"]),
                pollination_date=r["pollination_date"],
                created_at=r["created_at"],
            )
            for r in rows
        ]


@server.tool
def search_customer_reports(
    query: str,
    top_k: int = 5,
    site_id: str | None = None,
) -> list[CustomerReportSnippet]:
    """Semantic search over apiary inspection customer reports.

    Use for questions like "what did we see at the O'Reilly orchard?" or
    "where have we seen varroa above 3 this season?". Results are
    cosine-similarity chunks from the RAG corpus, newest-first at equal
    score.
    """
    with session_scope() as db:
        provider = VectorSearchProvider(db=db)
        filters: dict[str, object] = {}
        if site_id is not None:
            filters["site_id"] = site_id
        hits = provider.search(query, top_k=top_k, filters=filters)
        if not hits:
            return []
        # Enrich with site name + visit date from the source customer_report.
        chunk_ids = [uuid.UUID(h.chunk_id) for h in hits]
        meta_rows = (
            db.execute(
                text(
                    "SELECT c.id AS chunk_id, tr.visit_date, cs.name AS site_name "
                    "FROM chunks c "
                    "JOIN documents d ON d.id = c.document_id "
                    "LEFT JOIN customer_reports tr ON tr.id = d.source_id "
                    "LEFT JOIN customer_sites cs ON cs.id = tr.customer_site_id "
                    "WHERE c.id = ANY(:ids)"
                ),
                {"ids": chunk_ids},
            )
            .mappings()
            .all()
        )
        by_chunk = {str(r["chunk_id"]): r for r in meta_rows}
        return [
            CustomerReportSnippet(
                chunk_id=h.chunk_id,
                content=h.content,
                score=h.score,
                site_name=(by_chunk.get(h.chunk_id) or {}).get("site_name"),
                visit_date=(by_chunk.get(h.chunk_id) or {}).get("visit_date"),
            )
            for h in hits
        ]


@server.tool
def draft_followup_email(
    customer_id: str,
    topic: str,
    tone: str = "professional",
) -> DraftedEmail:
    """Draft a follow-up email to a customer about a topic.

    Pulls the customer's most-recent order + most-recent customer report as
    context, then asks the LLM for a subject + body. Tone: one of
    `professional`, `warm`, `urgent`.
    """
    if tone not in {"professional", "warm", "urgent"}:
        raise ValueError(
            f"Unknown tone {tone!r}; expected professional|warm|urgent."
        )
    with session_scope() as db:
        cust = db.execute(
            text("SELECT name, market_segment FROM customers WHERE id = :id"),
            {"id": customer_id},
        ).first()
        if cust is None:
            raise LookupError(f"No customer with id={customer_id!r}.")
        customer_name, segment = cust[0], cust[1]

        last_order = (
            db.execute(
                text(
                    "SELECT state, total_usd, pollination_date, created_at "
                    "FROM orders WHERE customer_id = :cid "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"cid": customer_id},
            )
            .mappings()
            .first()
        )
        last_trip = (
            db.execute(
                text(
                    "SELECT visit_date, hives_placed, varroa_count, narrative "
                    "FROM customer_reports tr JOIN customer_sites cs "
                    "  ON cs.id = tr.customer_site_id "
                    "WHERE cs.customer_id = :cid "
                    "ORDER BY tr.visit_date DESC LIMIT 1"
                ),
                {"cid": customer_id},
            )
            .mappings()
            .first()
        )

    context = _format_email_context(customer_name, segment, last_order, last_trip)
    llm = LLMClient()
    prompt = (
        f"You are drafting a follow-up email to {customer_name} "
        f"(market segment: {segment}) about: {topic}.\n\n"
        f"Tone: {tone}.\n\n"
        f"Context:\n{context}\n\n"
        f"Return JSON with exactly two keys: `subject` (≤ 80 chars) and "
        f"`body_text` (plain text, 2-4 short paragraphs, no signature)."
    )
    resp = llm.complete(prompt, max_tokens=600)
    subject, body = _parse_subject_body(resp.text)
    return DraftedEmail(subject=subject, body_text=body, tone=tone)  # type: ignore[arg-type]


def _format_email_context(
    customer_name: str,
    segment: str,
    last_order: Any,
    last_trip: Any,
) -> str:
    """Format rows for prompt context. Accepts RowMapping or dict-like.

    `Any` here rather than `Mapping[str, Any]` because SQLAlchemy's
    `RowMapping` doesn't structurally satisfy `Mapping` under strict typing.
    """
    parts = [f"Customer: {customer_name} ({segment})"]
    if last_order is not None:
        parts.append(
            f"Last order: state={last_order['state']}, "
            f"total=${last_order['total_usd']}, "
            f"pollination_date={last_order['pollination_date']}, "
            f"created_at={last_order['created_at']}"
        )
    else:
        parts.append("Last order: (none)")
    if last_trip is not None:
        parts.append(
            f"Last customer report: visit_date={last_trip['visit_date']}, "
            f"hives_placed={last_trip['hives_placed']}, "
            f"varroa_count={last_trip['varroa_count']}, "
            f"narrative: {last_trip['narrative'][:300]}"
        )
    else:
        parts.append("Last customer report: (none)")
    return "\n".join(parts)


def _parse_subject_body(text: str) -> tuple[str, str]:
    """Best-effort JSON extraction.

    The LLM may return a fenced code block or raw JSON; strip fences and
    parse. If parsing fails, fall back to "Subject: ...\\n\\n<body>".
    """
    import json
    import re

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE)
    try:
        obj = json.loads(cleaned)
        return str(obj["subject"]), str(obj["body_text"])
    except (json.JSONDecodeError, KeyError, TypeError):
        # Fallback split
        lines = cleaned.split("\n", 1)
        subject = lines[0].removeprefix("Subject:").strip()
        body = lines[1].strip() if len(lines) > 1 else cleaned
        return subject, body


def main(argv: list[str] | None = None) -> int:
    """Entry point. `--sse` switches transport from stdio."""
    args = argv if argv is not None else sys.argv[1:]
    logging.basicConfig(level=logging.INFO)
    if "--sse" in args:
        server.run(transport="sse")
    else:
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
