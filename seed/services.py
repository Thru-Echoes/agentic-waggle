"""Four pollination-contract service SKUs."""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Service

SERVICES: list[Service] = [
    Service(
        sku="POL_ALMOND_PREMIUM",
        category="pollination",
        name="Almond bloom pollination — premium grade",
        pricing_model="per_hive_flat",
        default_rate_usd=Decimal("220.00"),
        description="8-frame minimum, 2-week deployment, varroa <3.0/100",
    ),
    Service(
        sku="POL_TREE_FRUIT",
        category="pollination",
        name="Tree-fruit pollination (apple/cherry/pear)",
        pricing_model="per_hive_flat",
        default_rate_usd=Decimal("130.00"),
        description="10–14 day deployment, 6-frame minimum",
    ),
    Service(
        sku="POL_BERRY",
        category="pollination",
        name="Berry pollination (blueberry/cranberry)",
        pricing_model="per_acre_per_week",
        default_rate_usd=Decimal("80.00"),
        description="Per acre per week, minimum 2-week placement",
    ),
    Service(
        sku="POL_CUSTOM",
        category="pollination",
        name="Custom pollination contract",
        pricing_model="negotiated",
        default_rate_usd=None,
        description="Negotiated terms; largest margin variance",
    ),
]


def generate() -> list[Service]:
    return SERVICES


def persist(db: Session, services: list[Service]) -> None:
    """Duplicate SKU raises IntegrityError."""
    for s in services:
        db.execute(
            text(
                "INSERT INTO services (id, sku, name, category, pricing_model, default_rate_usd, description) "
                "VALUES (:id, :sku, :name, :category, :pm, :rate, :desc)"
            ),
            {
                "id": str(uuid.uuid4()),
                "sku": s.sku,
                "name": s.name,
                "category": s.category,
                "pm": s.pricing_model,
                "rate": s.default_rate_usd,
                "desc": s.description,
            },
        )
