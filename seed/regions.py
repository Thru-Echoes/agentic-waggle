"""Six pollination regions following the US migratory beekeeping calendar."""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Region

REGIONS: list[Region] = [
    Region(
        code="central_valley",
        name="Central Valley (CA)",
        primary_crops=["almond", "stone_fruit"],
        peak_start_month=2,
        peak_end_month=3,
    ),
    Region(
        code="pacific_northwest",
        name="Pacific Northwest (WA, OR)",
        primary_crops=["apple", "cherry", "pear"],
        peak_start_month=4,
        peak_end_month=5,
    ),
    Region(
        code="mountain_west",
        name="Mountain West (UT, CO)",
        primary_crops=["cherry", "apple", "melon"],
        peak_start_month=5,
        peak_end_month=6,
    ),
    Region(
        code="midwest",
        name="Midwest (MI, IN, WI)",
        primary_crops=["blueberry", "apple", "cucurbit"],
        peak_start_month=5,
        peak_end_month=8,
    ),
    Region(
        code="northeast",
        name="Northeast (ME, NY, VT)",
        primary_crops=["blueberry", "apple", "cranberry"],
        peak_start_month=5,
        peak_end_month=10,
    ),
    Region(
        code="southeast",
        name="Southeast (GA, FL, NC)",
        primary_crops=["blueberry", "cucurbit", "citrus"],
        peak_start_month=3,
        peak_end_month=6,
    ),
]


def generate() -> list[Region]:
    return REGIONS


def persist(db: Session, regions: list[Region]) -> None:
    """Insert into the `territories` table. Duplicates raise."""
    for r in regions:
        db.execute(
            text(
                "INSERT INTO territories (id, code, name, metadata) "
                "VALUES (:id, :code, :name, CAST(:metadata AS jsonb))"
            ),
            {
                "id": str(uuid.uuid4()),
                "code": r.code,
                "name": r.name,
                "metadata": _serialize_metadata(r),
            },
        )


def _serialize_metadata(r: Region) -> str:
    import json

    return json.dumps(
        {
            "primary_crops": r.primary_crops,
            "peak_start_month": r.peak_start_month,
            "peak_end_month": r.peak_end_month,
        }
    )
