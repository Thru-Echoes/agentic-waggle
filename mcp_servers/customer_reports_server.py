"""Waggle — Trip Reports MCP server.

Tools for analyzing the apiary inspection corpus:
    * `extract_from_narrative(narrative)` — LLM structured extraction of
      hives_placed, varroa_count, diseases, pesticide exposure from free text.
    * `query_by_disease(disease_code, since)` — every report mentioning a
      specific disease, optionally filtered to visits after `since`.
    * `query_by_varroa_threshold(min_count)` — every report with varroa
      above a threshold.
    * `aggregate_fleet_health(region_code)` — averages + disease counts,
      optionally scoped to one region.

Run stdio transport:
    python -m mcp_servers.customer_reports_server

Run SSE:
    python -m mcp_servers.customer_reports_server --sse
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import sys
from datetime import date
from decimal import Decimal

from fastmcp import FastMCP
from sqlalchemy import text

from db.enums import DiseaseCode
from db.session import session_scope
from mcp_servers.schemas import (
    ExtractedCustomerReport,
    FleetHealthSummary,
    CustomerReportSummary,
)
from providers.llm import LLMClient

log = logging.getLogger(__name__)

server: FastMCP = FastMCP(
    "waggle-customer-reports",
    instructions=(
        "Trip Reports analyst for Waggle. Use these tools to extract "
        "structured fields from free-text apiary inspection narratives, "
        "query by disease or varroa threshold, and summarize fleet health "
        "across regions."
    ),
)


# ---------------------------------------------------------------------------
# Tool: extract_from_narrative
# ---------------------------------------------------------------------------


_EXTRACT_PROMPT_TEMPLATE = (
    "You are parsing a commercial beekeeper's field inspection note into "
    "structured data. Return JSON with exactly these keys:\n"
    "  * hives_placed (int, 0-2000)\n"
    "  * varroa_count (decimal, mites per 100 bees, 0-50; report 0 if "
    "the narrative says the count was clean)\n"
    "  * diseases_observed (list; subset of "
    "[\"AFB\", \"EFB\", \"NOSEMA\", \"SHB\", \"TROPILAELAPS\", \"NONE\"]. "
    "Use [\"NONE\"] only when the note explicitly says no disease; use an "
    "empty list if the note is silent on disease)\n"
    "  * pesticide_exposure_note (string | null — copy the relevant span if "
    "present; otherwise null)\n"
    "  * confidence (float 0-1; your confidence the numbers are right)\n\n"
    "Rules:\n"
    "  * Never invent numbers. If hives_placed isn't clear, return 0 and "
    "lower confidence accordingly.\n"
    "  * If the narrative contradicts itself, report the most recent "
    "observation and note that in pesticide_exposure_note.\n\n"
    "Narrative:\n{narrative}\n\n"
    "Return only the JSON object, no prose, no fences."
)


@server.tool
def extract_from_narrative(narrative: str) -> ExtractedCustomerReport:
    """Extract structured customer-report fields from free-text narrative.

    Calls the real LLM (no canned fallback). Raises if no LLM API key is
    set or the LLM returns unparseable output.
    """
    if not narrative.strip():
        raise ValueError("narrative must be non-empty.")
    llm = LLMClient()
    resp = llm.complete(
        _EXTRACT_PROMPT_TEMPLATE.format(narrative=narrative),
        max_tokens=500,
    )
    import json
    import re

    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        resp.text.strip(),
        flags=re.MULTILINE,
    )
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"LLM returned unparseable JSON; got {cleaned[:200]!r}."
        ) from exc
    return ExtractedCustomerReport(
        hives_placed=int(obj["hives_placed"]),
        varroa_count=float(obj["varroa_count"]),
        diseases_observed=list(obj.get("diseases_observed", [])),
        pesticide_exposure_note=obj.get("pesticide_exposure_note"),
        confidence=float(obj["confidence"]),
    )


# ---------------------------------------------------------------------------
# Tool: query_by_disease
# ---------------------------------------------------------------------------


@server.tool
def query_by_disease(
    disease_code: str,
    since: str | None = None,
    limit: int = 50,
) -> list[CustomerReportSummary]:
    """Return every customer report mentioning the given disease code.

    `since` is an ISO date (YYYY-MM-DD); reports before it are excluded.
    `disease_code` must be one of AFB, EFB, NOSEMA, SHB, TROPILAELAPS.
    """
    valid = {d.value for d in DiseaseCode if d != DiseaseCode.NONE}
    if disease_code not in valid:
        raise ValueError(
            f"Unknown disease_code {disease_code!r}; expected one of {sorted(valid)}."
        )
    since_date = date.fromisoformat(since) if since else None
    sql = """
        SELECT tr.id, cs.name AS site_name, cs.customer_id,
               c.name AS customer_name, u.name AS author_name,
               tr.visit_date, tr.hives_placed, tr.varroa_count,
               tr.diseases_observed
        FROM customer_reports tr
        JOIN customer_sites cs ON cs.id = tr.customer_site_id
        JOIN customers c ON c.id = cs.customer_id
        JOIN users u ON u.id = tr.author_id
        WHERE tr.diseases_observed @> CAST(:disease_json AS jsonb)
          AND (:since IS NULL OR tr.visit_date >= :since)
        ORDER BY tr.visit_date DESC
        LIMIT :lim
    """
    params = {
        "disease_json": f'["{disease_code}"]',
        "since": since_date,
        "lim": limit,
    }
    with session_scope() as db:
        rows = db.execute(text(sql), params).mappings().all()
    return [
        CustomerReportSummary(
            customer_report_id=r["id"],
            customer_name=r["customer_name"],
            site_name=r["site_name"],
            author_name=r["author_name"],
            visit_date=r["visit_date"],
            hives_placed=r["hives_placed"],
            varroa_count=float(r["varroa_count"]),
            diseases_observed=list(r["diseases_observed"] or []),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Tool: query_by_varroa_threshold
# ---------------------------------------------------------------------------


@server.tool
def query_by_varroa_threshold(
    min_count: float,
    since: str | None = None,
    limit: int = 50,
) -> list[CustomerReportSummary]:
    """Every customer report with varroa_count ≥ min_count (mites per 100 bees)."""
    if min_count < 0:
        raise ValueError(f"min_count must be ≥ 0, got {min_count}")
    since_date = date.fromisoformat(since) if since else None
    sql = """
        SELECT tr.id, cs.name AS site_name, cs.customer_id,
               c.name AS customer_name, u.name AS author_name,
               tr.visit_date, tr.hives_placed, tr.varroa_count,
               tr.diseases_observed
        FROM customer_reports tr
        JOIN customer_sites cs ON cs.id = tr.customer_site_id
        JOIN customers c ON c.id = cs.customer_id
        JOIN users u ON u.id = tr.author_id
        WHERE tr.varroa_count >= :min_count
          AND (:since IS NULL OR tr.visit_date >= :since)
        ORDER BY tr.varroa_count DESC, tr.visit_date DESC
        LIMIT :lim
    """
    params = {"min_count": Decimal(str(min_count)), "since": since_date, "lim": limit}
    with session_scope() as db:
        rows = db.execute(text(sql), params).mappings().all()
    return [
        CustomerReportSummary(
            customer_report_id=r["id"],
            customer_name=r["customer_name"],
            site_name=r["site_name"],
            author_name=r["author_name"],
            visit_date=r["visit_date"],
            hives_placed=r["hives_placed"],
            varroa_count=float(r["varroa_count"]),
            diseases_observed=list(r["diseases_observed"] or []),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Tool: aggregate_fleet_health
# ---------------------------------------------------------------------------


@server.tool
def aggregate_fleet_health(
    region_code: str | None = None,
) -> FleetHealthSummary:
    """Aggregate inspection statistics.

    With no region_code, summarizes the whole fleet. With a region_code
    (e.g. "central_valley"), scopes to sites whose customer_sites.metadata
    references that region. Raises if the region_code doesn't match any
    territory.
    """
    scope = region_code or "fleet"
    with session_scope() as db:
        if region_code is not None:
            # Validate region exists; fail loud rather than return zeros.
            territory = db.execute(
                text("SELECT 1 FROM territories WHERE code = :code"),
                {"code": region_code},
            ).first()
            if territory is None:
                raise LookupError(
                    f"No territory with code={region_code!r}. "
                    "Use aggregate_fleet_health() with no arg for fleet-wide."
                )

        # Scoped report selection (customer_sites.metadata->>'region_code').
        base_where = (
            "WHERE (:region IS NULL OR "
            "cs.metadata->>'region_code' = :region)"
        )
        sql_stats = f"""
            SELECT
                COUNT(*) AS n,
                COALESCE(AVG(tr.varroa_count), 0) AS avg_v,
                COALESCE(MAX(tr.varroa_count), 0) AS max_v,
                COUNT(*) FILTER (WHERE tr.varroa_count > 3) AS sites_alarm
            FROM customer_reports tr
            JOIN customer_sites cs ON cs.id = tr.customer_site_id
            {base_where}
        """
        stats = db.execute(text(sql_stats), {"region": region_code}).mappings().first()

        sql_diseases = f"""
            SELECT jsonb_array_elements_text(tr.diseases_observed) AS d, COUNT(*)
            FROM customer_reports tr
            JOIN customer_sites cs ON cs.id = tr.customer_site_id
            {base_where}
            GROUP BY d
        """
        disease_rows = db.execute(text(sql_diseases), {"region": region_code}).all()

    return FleetHealthSummary(
        scope=scope,
        reports_considered=int(stats["n"]) if stats else 0,
        avg_varroa=float(stats["avg_v"]) if stats else 0.0,
        max_varroa=float(stats["max_v"]) if stats else 0.0,
        disease_counts={d: int(n) for d, n in disease_rows},
        sites_with_varroa_above_3=int(stats["sites_alarm"]) if stats else 0,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    logging.basicConfig(level=logging.INFO)
    if "--sse" in args:
        server.run(transport="sse")
    else:
        server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
