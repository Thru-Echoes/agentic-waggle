"""Product data sheets — LLM-authored reference docs for ~10 SKUs.

These don't have their own domain table. Each entry is persisted as a
`documents` row with `source_type='product_data_sheet'` so the content
enters the RAG corpus and the Sales Copilot MCP tool can answer
catalog-and-compliance questions semantically.

Reads from `seed/fixtures/product_data_sheets.json` (generate via
`python -m scripts.generate_fixtures`).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "product_data_sheets.json"


class FixtureMissing(FileNotFoundError):
    """Raised when seed/fixtures/product_data_sheets.json is missing."""


def _load_fixture() -> list[dict]:
    if not FIXTURE_PATH.exists():
        raise FixtureMissing(
            f"Product data-sheet fixture missing at {FIXTURE_PATH}. "
            f"Generate it via `python -m scripts.generate_fixtures`."
        )
    return json.loads(FIXTURE_PATH.read_text())


def persist(db: Session) -> int:
    """Insert one `documents` row per data sheet. Returns count.

    Resolves each sheet's `product_sku` to the matching `products.id`
    so the document's `source_id` references a real product.
    Raises LookupError if a referenced SKU is missing — fail-loud.
    """
    sheets = _load_fixture()

    sku_rows = db.execute(
        text("SELECT id, sku FROM products")
    ).mappings().all()
    sku_to_id = {r["sku"]: r["id"] for r in sku_rows}

    for s in sheets:
        product_id = sku_to_id.get(s["product_sku"])
        if product_id is None:
            raise LookupError(
                f"Product data sheet references unknown SKU "
                f"{s['product_sku']!r}. Did products.persist run first?"
            )

        db.execute(
            text(
                "INSERT INTO documents "
                "(id, source_type, source_id, content, metadata) "
                "VALUES (:id, 'product_data_sheet', :src_id, :content, "
                "        CAST(:meta AS jsonb))"
            ),
            {
                "id": str(uuid.uuid4()),
                "src_id": product_id,
                "content": s["content"],
                "meta": json.dumps(
                    {
                        "source_type": "product_data_sheet",
                        "product_sku": s["product_sku"],
                        "product_name": s["product_name"],
                        "category": s["category"],
                        "is_regulated": s["is_regulated"],
                    }
                ),
            },
        )
    return len(sheets)
