"""Customer sites — each customer gets 1–4 sites with acreage + bloom windows.

Orchards produce multi-block sites; wholesalers/specialty/mead/peer customers
each have 1 site typically.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Customer, CustomerSite, Region

# Rough (lat,lng) anchors per region so coordinates cluster realistically.
REGION_ANCHORS: dict[str, tuple[float, float]] = {
    "central_valley": (37.6391, -120.9969),
    "pacific_northwest": (47.4235, -120.3103),
    "mountain_west": (39.3210, -111.0937),
    "midwest": (44.3148, -85.6024),
    "northeast": (44.3106, -71.8493),
    "southeast": (33.1960, -82.7901),
}

# Bloom windows vary per crop family.
CROP_BLOOM: dict[str, tuple[int, int]] = {
    "almond": (2, 3),
    "apple": (4, 5),
    "cherry": (4, 5),
    "pear": (4, 5),
    "blueberry": (5, 6),
    "cranberry": (6, 7),
    "stone_fruit": (3, 4),
    "melon": (5, 8),
    "cucurbit": (6, 8),
    "citrus": (3, 5),
}


def _sites_count_for_segment(segment: str, faker: Faker) -> int:
    if segment.startswith("ORCHARD_"):
        return faker.random_int(min=2, max=4)
    if segment in ("HONEY_WHOLESALER", "PEER_BEEKEEPER"):
        return faker.random_int(min=1, max=2)
    return 1


def _region_for_customer(segment: str, faker: Faker) -> str:
    orchard_regions = ["central_valley", "pacific_northwest", "mountain_west", "midwest", "northeast", "southeast"]
    if segment == "ORCHARD_ALMOND":
        return "central_valley"
    if segment == "ORCHARD_TREE_FRUIT":
        return faker.random_element(["pacific_northwest", "mountain_west", "midwest"])
    if segment == "ORCHARD_BERRY":
        return faker.random_element(["midwest", "northeast", "southeast"])
    return faker.random_element(orchard_regions)


def _crops_for_segment(segment: str) -> list[str]:
    return {
        "ORCHARD_ALMOND": ["almond"],
        "ORCHARD_TREE_FRUIT": ["apple", "cherry", "pear"],
        "ORCHARD_BERRY": ["blueberry", "cranberry"],
    }.get(segment, [])


def generate(faker: Faker, customers: list[Customer], regions: list[Region]) -> list[CustomerSite]:
    valid_region_codes = {r.code for r in regions}
    sites: list[CustomerSite] = []
    for customer in customers:
        n = _sites_count_for_segment(customer.market_segment, faker)
        region_code = _region_for_customer(customer.market_segment, faker)
        assert region_code in valid_region_codes, f"unknown region {region_code}"

        for i in range(n):
            if customer.market_segment.startswith("ORCHARD_"):
                block_name = faker.random_element(
                    ["North Block", "South Block", "East Block", "West Block",
                     "Orchard Rd", "River Block", "Hilltop", "Highway Block"]
                )
                name = f"{block_name} {i + 1}"
                crops = _crops_for_segment(customer.market_segment)
                acreage = {c: faker.random_int(40, 200) for c in crops}
                bloom = CROP_BLOOM[crops[0]] if crops else (3, 5)
            else:
                name = "Main Warehouse" if i == 0 else f"Warehouse {i + 1}"
                acreage = {}
                bloom = (3, 10)

            anchor_lat, anchor_lng = REGION_ANCHORS[region_code]
            lat = anchor_lat + faker.random_int(-100, 100) / 100.0
            lng = anchor_lng + faker.random_int(-100, 100) / 100.0

            sites.append(
                CustomerSite(
                    customer_name=customer.name,
                    name=name,
                    region_code=region_code,
                    acreage_by_crop=acreage,
                    bloom_start_month=bloom[0],
                    bloom_end_month=bloom[1],
                    latitude=round(lat, 4),
                    longitude=round(lng, 4),
                )
            )
    return sites


def persist(db: Session, sites: list[CustomerSite]) -> None:
    """Insert into `customer_sites`. Unknown customer_name raises LookupError."""
    crows = db.execute(text("SELECT id, name FROM customers")).mappings().all()
    name_to_id = {r["name"]: r["id"] for r in crows}

    for s in sites:
        if s.customer_name not in name_to_id:
            raise LookupError(
                f"Site {s.name!r} references unknown customer {s.customer_name!r}. "
                f"Did customers.persist run first?"
            )
        db.execute(
            text(
                "INSERT INTO customer_sites "
                "(id, customer_id, name, acreage_by_crop, metadata) "
                "VALUES (:id, :cid, :name, CAST(:acreage AS jsonb), CAST(:meta AS jsonb))"
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": name_to_id[s.customer_name],
                "name": s.name,
                "acreage": json.dumps(s.acreage_by_crop),
                "meta": json.dumps({
                    "region_code": s.region_code,
                    "bloom_start_month": s.bloom_start_month,
                    "bloom_end_month": s.bloom_end_month,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                }),
            },
        )
