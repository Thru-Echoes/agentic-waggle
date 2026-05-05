"""40 apiary inspection reports — the customer-report corpus.

Narratives + tags are LLM-authored and live in
`seed/fixtures/customer_reports.json`. The fixture is regenerated on
demand via `python -m scripts.generate_fixtures`. The seed pipeline
reads the fixture verbatim — diversity is in the file, not in this
module.

Each report row has both a domain-table entry (customer_reports) and
a parallel documents-table row (source_type='customer_report') so the
narrative is part of the RAG corpus. Document metadata is enriched
with structured fields (varroa, diseases, tags, etc.) so the chunker
can propagate them down for hybrid filter+semantic retrieval.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Apiarist, CustomerSite, InspectionReport

log = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "customer_reports.json"


class FixtureMissing(FileNotFoundError):
    """Raised when seed/fixtures/customer_reports.json is missing.

    Generate it via `python -m scripts.generate_fixtures` (requires
    OPENAI_API_KEY).
    """


def _load_fixture() -> list[dict]:
    if not FIXTURE_PATH.exists():
        raise FixtureMissing(
            f"Customer-report fixture missing at {FIXTURE_PATH}. "
            f"Generate it via `python -m scripts.generate_fixtures` "
            f"(requires OPENAI_API_KEY in .env)."
        )
    return json.loads(FIXTURE_PATH.read_text())


def generate(
    faker: Faker,
    sites: list[CustomerSite],
    apiarists: list[Apiarist],
) -> list[InspectionReport]:
    """Return InspectionReport models from the LLM-authored fixture.

    `faker`, `sites`, and `apiarists` are accepted for backward compat
    with the test harness but the fixture is the source of truth — the
    same Faker(seed=42) chain produced both the fixture entries and
    these inputs, so the names and emails align.

    Side effects: filesystem read of the fixture file.
    """
    rows = _load_fixture()
    out: list[InspectionReport] = []
    for r in rows:
        out.append(
            InspectionReport(
                customer_name=r["customer_name"],
                site_name=r["site_name"],
                author_email=r["author_email"],
                visit_date=date.fromisoformat(r["visit_date"]),
                hives_placed=int(r["hives_placed"]),
                varroa_count=Decimal(str(r["varroa_count"])),
                diseases_observed=r.get("diseases_observed", ["NONE"]),
                pesticide_exposure_note=r.get("pesticide_exposure_note"),
                narrative=r["narrative"],
                tags=r.get("tags", []),
            )
        )
    return out


def persist(db: Session, inspections: list[InspectionReport]) -> None:
    """Insert customer_reports + parallel document rows.

    Side effects: SQL INSERTs against `customer_reports` and `documents`.
    Raises LookupError if site or author cannot be resolved.
    """
    site_rows = db.execute(
        text(
            "SELECT cs.id, cs.name AS site_name, c.name AS customer_name "
            "FROM customer_sites cs JOIN customers c ON cs.customer_id = c.id"
        )
    ).mappings().all()
    site_lookup = {(r["customer_name"], r["site_name"]): r["id"] for r in site_rows}

    user_rows = db.execute(text("SELECT id, email FROM users")).mappings().all()
    email_to_user_id = {r["email"]: r["id"] for r in user_rows}

    for ins in inspections:
        site_id = site_lookup.get((ins.customer_name, ins.site_name))
        if site_id is None:
            raise LookupError(
                f"Inspection references unknown site "
                f"({ins.customer_name!r}, {ins.site_name!r}). "
                f"Did sites.persist run first?"
            )
        author_id = email_to_user_id.get(ins.author_email)
        if author_id is None:
            raise LookupError(
                f"Inspection references unknown author {ins.author_email!r}. "
                f"Did apiarists.persist run first?"
            )

        cr_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO customer_reports "
                "(id, customer_site_id, author_id, visit_date, hives_placed, "
                " varroa_count, diseases_observed, pesticide_exposure_note, "
                " narrative, tags) "
                "VALUES (:id, :site_id, :author_id, :visit_date, :hives, "
                "        :varroa, CAST(:diseases AS jsonb), :pesticide, "
                "        :narrative, CAST(:tags AS jsonb))"
            ),
            {
                "id": cr_id,
                "site_id": site_id,
                "author_id": author_id,
                "visit_date": ins.visit_date,
                "hives": ins.hives_placed,
                "varroa": ins.varroa_count,
                "diseases": json.dumps(ins.diseases_observed),
                "pesticide": ins.pesticide_exposure_note,
                "narrative": ins.narrative,
                "tags": json.dumps(ins.tags),
            },
        )

        # Parallel document row so the narrative enters the RAG corpus.
        # Metadata is enriched so the chunker can propagate it onto each
        # chunk for hybrid filter+semantic retrieval.
        doc_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO documents "
                "(id, source_type, source_id, content, metadata) "
                "VALUES (:id, 'customer_report', :src_id, :content, "
                "        CAST(:meta AS jsonb))"
            ),
            {
                "id": doc_id,
                "src_id": cr_id,
                "content": ins.narrative,
                "meta": json.dumps(
                    {
                        "source_type": "customer_report",
                        "customer_site_id": str(site_id),
                        "customer_name": ins.customer_name,
                        "site_name": ins.site_name,
                        "visit_date": ins.visit_date.isoformat(),
                        "author_email": ins.author_email,
                        "hives_placed": ins.hives_placed,
                        "varroa_count": float(ins.varroa_count),
                        "diseases_observed": ins.diseases_observed,
                        "tags": ins.tags,
                    }
                ),
            },
        )
