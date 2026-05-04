"""60 product SKUs across six categories.

Curated list — deterministic, no RNG. Prices reflect rough 2026 wholesale
beekeeping-industry ranges for plausibility.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid
from decimal import Decimal

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Product

HONEY = [
    ("HONEY-WLD-A-DRUM", "Wildflower Grade A, 600 lb drum", "honey", "3300.00", False),
    ("HONEY-WLD-A-PAIL", "Wildflower Grade A, 60 lb pail", "honey", "420.00", False),
    ("HONEY-CLV-A-DRUM", "Clover Grade A, 600 lb drum", "honey", "3500.00", False),
    ("HONEY-CLV-A-PAIL", "Clover Grade A, 60 lb pail", "honey", "440.00", False),
    ("HONEY-ORG-A-DRUM", "Orange-blossom Grade A, 600 lb drum", "honey", "4100.00", False),
    ("HONEY-ORG-A-PAIL", "Orange-blossom Grade A, 60 lb pail", "honey", "520.00", False),
    ("HONEY-SAG-A-PAIL", "Sage Grade A, 60 lb pail", "honey", "560.00", False),
    ("HONEY-BKW-A-PAIL", "Buckwheat Grade A, 60 lb pail", "honey", "500.00", False),
    ("HONEY-TUP-A-PAIL", "Tupelo Grade A, 60 lb pail", "honey", "720.00", False),
    ("HONEY-ALF-A-DRUM", "Alfalfa Grade A, 600 lb drum", "honey", "3200.00", False),
    ("HONEY-ALF-A-PAIL", "Alfalfa Grade A, 60 lb pail", "honey", "410.00", False),
    ("HONEY-SRW-A-PAIL", "Sourwood Grade A, 60 lb pail", "honey", "800.00", False),
    ("HONEY-MAC-A-PAIL", "Macadamia-blossom Grade A, 60 lb pail", "honey", "680.00", False),
    ("HONEY-MAN-A-PAIL", "Manuka-style Grade A, 60 lb pail", "honey", "950.00", False),
    ("HONEY-WLD-B-DRUM", "Wildflower Grade B, 600 lb drum", "honey", "2700.00", False),
    ("HONEY-CLV-B-DRUM", "Clover Grade B, 600 lb drum", "honey", "2900.00", False),
    ("HONEY-WLD-RAW-JAR", "Wildflower raw, case of 12x 16oz jars", "honey", "110.00", False),
    ("HONEY-CLV-RAW-JAR", "Clover raw, case of 12x 16oz jars", "honey", "115.00", False),
    ("HONEY-ORG-RAW-JAR", "Orange-blossom raw, case of 12x 16oz jars", "honey", "140.00", False),
    ("HONEY-SAG-RAW-JAR", "Sage raw, case of 12x 16oz jars", "honey", "150.00", False),
]

QUEENS_AND_BEES = [
    ("QUEEN-ITA-MATED",    "Italian mated queen",              "queens_and_bees", "42.00",  True),
    ("QUEEN-CAR-MATED",    "Carniolan mated queen",            "queens_and_bees", "45.00",  True),
    ("QUEEN-RUS-MATED",    "Russian mated queen",              "queens_and_bees", "50.00",  True),
    ("QUEEN-SAS-MATED",    "Saskatraz mated queen",            "queens_and_bees", "52.00",  True),
    ("QUEEN-VSH-MATED",    "VSH Pol-line mated queen",         "queens_and_bees", "60.00",  True),
    ("QUEEN-BKF-MATED",    "Buckfast mated queen",             "queens_and_bees", "48.00",  True),
    ("QUEEN-ITA-VIRG",     "Italian virgin queen",             "queens_and_bees", "22.00",  True),
    ("QUEEN-ITA-CELL",     "Italian queen cell",               "queens_and_bees", "8.00",   False),
    ("PKG-BEES-3LB-ITA",   "3 lb package bees w/ Italian queen","queens_and_bees","165.00",  True),
    ("PKG-BEES-5LB-ITA",   "5 lb package bees w/ Italian queen","queens_and_bees","235.00",  True),
    ("NUC-5F-ITA",         "5-frame nucleus colony, Italian",   "queens_and_bees", "210.00", False),
    ("NUC-10F-CAR",        "10-frame nucleus colony, Carniolan","queens_and_bees", "340.00", False),
    ("HIVE-LANG-8F-FULL",  "Full Langstroth 8-frame hive",      "queens_and_bees", "420.00", False),
    ("HIVE-LANG-10F-FULL", "Full Langstroth 10-frame hive",     "queens_and_bees", "460.00", False),
    ("HIVE-TOPBAR-FULL",   "Full top-bar hive",                 "queens_and_bees", "390.00", False),
]

EQUIPMENT = [
    ("EQ-BOT-8F",    "Bottom board, 8-frame",                "equipment",  "28.00",  False),
    ("EQ-BOT-10F",   "Bottom board, 10-frame",               "equipment",  "32.00",  False),
    ("EQ-FRAME-DP",  "Deep frame (unassembled)",             "equipment",  "3.20",   False),
    ("EQ-FRAME-MD",  "Medium frame (unassembled)",           "equipment",  "2.80",   False),
    ("EQ-FND-WAX",   "Wax foundation sheet",                 "equipment",  "2.10",   False),
    ("EQ-FND-PLAS",  "Plastic foundation sheet",             "equipment",  "1.60",   False),
    ("EQ-FEED-TOP",  "Top feeder",                           "equipment",  "18.00",  False),
    ("EQ-FEED-ENT",  "Entrance feeder",                      "equipment",  "8.00",   False),
    ("EQ-FEED-DIV",  "Division board feeder",                "equipment",  "14.00",  False),
    ("EQ-EXCL-8F",   "Queen excluder, 8-frame",              "equipment",  "24.00",  False),
    ("EQ-EXCL-10F",  "Queen excluder, 10-frame",             "equipment",  "28.00",  False),
    ("EQ-COV-INNER", "Inner cover",                          "equipment",  "22.00",  False),
    ("EQ-COV-TEL",   "Telescoping cover",                    "equipment",  "38.00",  False),
    ("EQ-SUIT-FULL", "Full bee suit with veil",              "equipment",  "140.00", False),
    ("EQ-GLOVES-LG", "Protective gloves, large",             "equipment",  "22.00",  False),
]

BEESWAX = [
    ("WAX-RAW-10",   "Raw beeswax, 10 lb block",             "beeswax_products", "95.00",  False),
    ("WAX-FIL-10",   "Filtered beeswax, 10 lb block",        "beeswax_products", "130.00", False),
    ("PROPOLIS-8OZ", "Propolis tincture, 8 oz",              "beeswax_products", "45.00",  False),
    ("RJ-4OZ",       "Royal jelly, 4 oz",                    "beeswax_products", "85.00",  False),
    ("POLLEN-1LB",   "Bee-collected pollen, 1 lb",           "beeswax_products", "28.00",  False),
]

CONSUMABLES = [
    ("FEED-SYR-50",  "2:1 sugar syrup, 50 gal",              "consumables", "145.00", False),
    ("FEED-FON-50",  "Winter fondant, 50 lb",                "consumables", "95.00",  False),
    ("FEED-POL-10",  "Pollen substitute patty, 10 lb case",  "consumables", "65.00",  False),
]

TREATMENTS = [
    ("TRT-OXALIC-R", "Oxalic acid dihydrate (registered)",   "treatments", "55.00", True),
    ("TRT-FORMIC-R", "Formic acid (registered)",             "treatments", "72.00", True),
]


ALL_PRODUCTS = HONEY + QUEENS_AND_BEES + EQUIPMENT + BEESWAX + CONSUMABLES + TREATMENTS


def generate(faker: Faker) -> list[Product]:
    # faker kept in signature for consistency; one RNG call keeps sequence aligned.
    faker.random_int(0, 1)
    out: list[Product] = []
    for sku, name, category, price, regulated in ALL_PRODUCTS:
        out.append(
            Product(
                sku=sku,
                category=category,  # type: ignore[arg-type]
                name=name,
                price_usd=Decimal(price),
                is_regulated=regulated,
            )
        )
    return out


def persist(db: Session, products: list[Product]) -> None:
    """Insert into `products`. Duplicate SKU raises IntegrityError."""
    for p in products:
        db.execute(
            text(
                "INSERT INTO products (id, sku, name, category, price_usd, is_regulated) "
                "VALUES (:id, :sku, :name, :category, :price, :regulated)"
            ),
            {
                "id": str(uuid.uuid4()),
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "price": p.price_usd,
                "regulated": p.is_regulated,
            },
        )
