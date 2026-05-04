"""40 customers across seven market segments, with deliberate name edge cases
for the fuzzy-matching demo.

Segment distribution (rounded to 40 total):
  ORCHARD_ALMOND      12   (30%)
  ORCHARD_TREE_FRUIT   8   (20%)
  ORCHARD_BERRY        4   (10%)
  HONEY_WHOLESALER     6   (15%)
  SPECIALTY_FOOD       4   (10%)
  MEAD_WINERY          3    (8%)
  PEER_BEEKEEPER       3    (7%)
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.faker_providers import EDGE_CASE_CUSTOMERS
from seed.models import Customer, MarketSegment

SEGMENT_TARGETS = {
    "ORCHARD_ALMOND": 12,
    "ORCHARD_TREE_FRUIT": 8,
    "ORCHARD_BERRY": 4,
    "HONEY_WHOLESALER": 6,
    "SPECIALTY_FOOD": 4,
    "MEAD_WINERY": 3,
    "PEER_BEEKEEPER": 3,
}


def _make_erp_no(idx: int) -> str:
    return f"C-{idx:05d}"


def generate(faker: Faker) -> list[Customer]:
    out: list[Customer] = []
    erp_seq = 1001

    # 1. Seed edge-case customers first so they're always present regardless
    #    of how Faker RNG plays out.
    for canonical, aliases in EDGE_CASE_CUSTOMERS:
        out.append(
            Customer(
                name=canonical,
                erp_customer_no=_make_erp_no(erp_seq),
                market_segment=_segment_for_edge_case(canonical),
                aliases=aliases,
            )
        )
        erp_seq += 1

    # 2. Fill the rest by segment target.
    edge_names = {c[0] for c in EDGE_CASE_CUSTOMERS}
    by_segment: dict[str, int] = {}
    for c in out:
        by_segment[c.market_segment] = by_segment.get(c.market_segment, 0) + 1

    for segment, target in SEGMENT_TARGETS.items():
        existing = by_segment.get(segment, 0)
        needed = max(0, target - existing)
        for _ in range(needed):
            name = _name_for_segment(faker, segment)
            # Avoid duplicates
            retries = 0
            while name in edge_names and retries < 5:
                name = _name_for_segment(faker, segment)
                retries += 1
            edge_names.add(name)
            out.append(
                Customer(
                    name=name,
                    erp_customer_no=_make_erp_no(erp_seq),
                    market_segment=segment,  # type: ignore[arg-type]
                    aliases=[],
                )
            )
            erp_seq += 1

    return out


def _name_for_segment(faker: Faker, segment: str) -> str:
    if segment in ("ORCHARD_ALMOND", "ORCHARD_TREE_FRUIT", "ORCHARD_BERRY"):
        return faker.orchard_name()
    if segment == "HONEY_WHOLESALER":
        return faker.wholesaler_name()
    if segment == "SPECIALTY_FOOD":
        return faker.specialty_name()
    if segment == "MEAD_WINERY":
        return faker.meadery_name()
    if segment == "PEER_BEEKEEPER":
        return faker.peer_beekeeper_name()
    return faker.company()


def _segment_for_edge_case(name: str) -> MarketSegment:
    # Edge cases chosen so each represents a different segment flavor.
    mapping: dict[str, MarketSegment] = {
        "O'Reilly Family Orchards": "ORCHARD_ALMOND",
        "Cascade Orchard Partners": "ORCHARD_TREE_FRUIT",
        "Five Rivers Apiaries": "PEER_BEEKEEPER",
        "St. Agnes Pollination": "ORCHARD_ALMOND",
        "Martinez & Sons Orchards": "ORCHARD_BERRY",
    }
    return mapping.get(name, "ORCHARD_ALMOND")


def persist(db: Session, customers: list[Customer]) -> None:
    """Insert into `customers`. Duplicate erp_customer_no raises IntegrityError."""
    for c in customers:
        db.execute(
            text(
                "INSERT INTO customers (id, name, erp_customer_no, aliases, market_segment) "
                "VALUES (:id, :name, :erp_no, CAST(:aliases AS jsonb), :segment)"
            ),
            {
                "id": str(uuid.uuid4()),
                "name": c.name,
                "erp_no": c.erp_customer_no,
                "aliases": json.dumps(c.aliases),
                "segment": c.market_segment,
            },
        )
