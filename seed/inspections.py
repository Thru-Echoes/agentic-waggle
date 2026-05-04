"""40 apiary inspection reports — the customer-report corpus.

Narratives are rendered from a handful of Jinja2 templates with slot-filled
structured data so they read like real rep notes. Templates live in
`seed/fixtures/narrative_templates/` and are loaded by filename.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Apiarist, CustomerSite, DiseaseCode, InspectionReport

TEMPLATE_DIR = Path(__file__).parent / "fixtures" / "narrative_templates"

# Fallback: inline templates used when the template dir is empty (so the
# seed works out of the box; authored templates override these).
INLINE_TEMPLATES = [
    """Visited {{ customer }} {{ site }} on {{ visit_date }}. {{ hives }} hives ({{ format }}) placed in {{ corner }} corner. \
{% if bloom_pct %}{{ crop }} bloom approximately {{ bloom_pct }}% open — {{ flow }} pollen flow observed.{% endif %} \
Sampled {{ sample }} hives for queen status: {{ queen_right }} queen-right with {{ brood }} frames brood. \
Varroa count {{ varroa }} mites per 100 bees ({{ varroa_verdict }}). \
{% if disease %}{{ disease }} indicators observed.{% else %}No disease indicators observed.{% endif %} \
{% if pesticide %}Pesticide note: {{ pesticide }}.{% else %}No pesticide concerns raised.{% endif %} \
Next visit scheduled {{ next_visit }}.""",

    """Routine inspection {{ site }} ({{ customer }}), {{ visit_date }}. {{ hives }} colonies present. \
Worker population averaging {{ population }}k per hive. \
{% if disease %}Disease watch: {{ disease }}.{% else %}Brood patterns solid; no disease watch.{% endif %} \
Varroa {{ varroa }}/100; {{ varroa_verdict }}. \
{% if owner_note %}Owner reports: {{ owner_note }}.{% endif %} \
{% if pesticide %}Note: {{ pesticide }}.{% endif %}""",
]


def _load_templates() -> list[str]:
    if TEMPLATE_DIR.exists() and any(TEMPLATE_DIR.glob("*.j2")):
        # Read raw sources directly so downstream `_render(template_src, ...)`
        # can call Template(src) again. Environment.get_template(...).source
        # is not part of the public Jinja API; reading the file is.
        return [p.read_text() for p in TEMPLATE_DIR.glob("*.j2")]
    return INLINE_TEMPLATES


def _render(template_src: str, **ctx) -> str:
    from jinja2 import Template

    return Template(template_src).render(**ctx).strip()


def _pick_diseases(faker: Faker) -> list[DiseaseCode]:
    # Most inspections are clean; ~15% show something.
    roll = faker.random_int(1, 100)
    if roll < 85:
        return ["NONE"]
    return [faker.random_element(["AFB", "EFB", "NOSEMA", "SHB"])]


def _varroa_verdict(count: Decimal) -> str:
    if count < Decimal("2"):
        return "well below treatment threshold"
    if count < Decimal("3"):
        return "approaching treatment threshold"
    return "exceeds treatment threshold, treatment planned"


def generate(
    faker: Faker,
    sites: list[CustomerSite],
    apiarists: list[Apiarist],
) -> list[InspectionReport]:
    templates = _load_templates()
    field_apiarists = [a for a in apiarists if a.role == "apiarist"]

    out: list[InspectionReport] = []
    target = 40
    anchor = date.today()

    for _ in range(target):
        site = faker.random_element(sites)
        author = faker.random_element(field_apiarists)
        visit_date = anchor - timedelta(days=faker.random_int(0, 365))

        crop_key = next(iter(site.acreage_by_crop.keys()), "almond")
        hives = faker.random_int(80, 500)
        varroa = Decimal(str(round(faker.random_int(50, 350) / 100.0, 2)))
        diseases = _pick_diseases(faker)

        pesticide_note: str | None = None
        if faker.random_int(1, 100) < 20:
            pesticide_note = faker.random_element([
                "Neighbor orchard sprayed dormant oil last Thursday; no bloom impact.",
                "Grower confirmed no spray during bloom per county NOI filing.",
                "Flag: pesticide applicator truck visible on adjacent block at dawn.",
            ])

        ctx = {
            "customer": site.customer_name,
            "site": site.name,
            "visit_date": visit_date.isoformat(),
            "hives": hives,
            "format": faker.hive_format(),
            "corner": faker.random_element(["north", "south", "east", "west", "center"]),
            "bloom_pct": faker.random_int(20, 95) if crop_key in ("almond", "apple", "cherry", "blueberry") else None,
            "crop": crop_key,
            "flow": faker.random_element(["heavy", "moderate", "light"]),
            "sample": faker.random_int(10, 40),
            "queen_right": faker.random_int(15, 40),
            "brood": faker.random_int(2, 6),
            "varroa": varroa,
            "varroa_verdict": _varroa_verdict(varroa),
            "disease": diseases[0] if diseases and diseases[0] != "NONE" else None,
            "pesticide": pesticide_note,
            "next_visit": (visit_date + timedelta(days=14)).isoformat(),
            "population": faker.random_int(25, 55),
            "owner_note": faker.random_element([
                None, None, None,
                "two hives collapsed overnight; suspect wind exposure",
                "requesting additional hives next season",
                "mentioned considering switch to organic cert next year",
            ]),
        }

        narrative = _render(faker.random_element(templates), **ctx)

        out.append(
            InspectionReport(
                customer_name=site.customer_name,
                site_name=site.name,
                author_email=author.email,
                visit_date=visit_date,
                hives_placed=hives,
                varroa_count=varroa,
                diseases_observed=diseases,
                pesticide_exposure_note=pesticide_note,
                narrative=narrative,
            )
        )
    return out


def persist(db: Session, inspections: list[InspectionReport]) -> None:
    # Resolve customer_site_id and author_id
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

        tr_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO customer_reports "
                "(id, customer_site_id, author_id, visit_date, hives_placed, "
                " varroa_count, diseases_observed, pesticide_exposure_note, narrative) "
                "VALUES (:id, :site_id, :author_id, :visit_date, :hives, "
                "        :varroa, CAST(:diseases AS jsonb), :pesticide, :narrative)"
            ),
            {
                "id": tr_id,
                "site_id": site_id,
                "author_id": author_id,
                "visit_date": ins.visit_date,
                "hives": ins.hives_placed,
                "varroa": ins.varroa_count,
                "diseases": json.dumps(ins.diseases_observed),
                "pesticide": ins.pesticide_exposure_note,
                "narrative": ins.narrative,
            },
        )

        # Corresponding document row so the RAG pipeline has something to
        # chunk + embed.
        doc_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO documents (id, source_type, source_id, content, metadata) "
                "VALUES (:id, 'customer_report', :src_id, :content, CAST(:meta AS jsonb))"
            ),
            {
                "id": doc_id,
                "src_id": tr_id,
                "content": ins.narrative,
                "meta": json.dumps(
                    {
                        "customer_name": ins.customer_name,
                        "site_name": ins.site_name,
                        "visit_date": ins.visit_date.isoformat(),
                        "author_email": ins.author_email,
                        "hives_placed": ins.hives_placed,
                    }
                ),
            },
        )
