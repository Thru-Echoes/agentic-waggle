"""Resolve a free-text line-item description to a concrete Product or Service SKU.

Three strategies, tried in order:
    1. **Exact SKU** — the email literally cites `POL_ALMOND_PREMIUM` or
       similar. Case-insensitive equality match against `products.sku`
       and `services.sku`.
    2. **Fuzzy name** — rapidfuzz over `product.name` + `service.name`.
       Score above `FUZZY_SCORE_MIN` wins.
    3. **LLM guess** — the agent falls back to an LLM call with the full
       SKU catalog in context. Documented as `match_method="llm_guess"`
       on the `ResolvedLineItem` so the review UI can flag it.

All three strategies return the same `ResolvedLineItem` shape.

Side effects: SELECTs `products` + `services`; strategy (3) makes one LLM call.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz, process
from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.schemas import RequestedLineItem, ResolvedLineItem

FUZZY_SCORE_MIN = 72.0


def _load_catalog(db: Session) -> list[dict[str, Any]]:
    """Return every product + service as dicts with kind, id, sku, name, price."""
    rows: list[dict[str, Any]] = []
    for row in db.execute(
        text("SELECT id, sku, name, price_usd FROM products")
    ).mappings():
        rows.append(
            {
                "kind": "product",
                "id": row["id"],
                "sku": row["sku"],
                "name": row["name"],
                "price": Decimal(row["price_usd"]),
            }
        )
    for row in db.execute(
        text("SELECT id, sku, name, default_rate_usd FROM services")
    ).mappings():
        price = row["default_rate_usd"]
        rows.append(
            {
                "kind": "service",
                "id": row["id"],
                "sku": row["sku"],
                "name": row["name"],
                "price": Decimal(price) if price is not None else Decimal("0"),
            }
        )
    return rows


def _build_resolved(
    entry: dict[str, Any],
    req: RequestedLineItem,
    method: str,
) -> ResolvedLineItem:
    price = req.mentioned_unit_price_usd or entry["price"]
    return ResolvedLineItem(
        sku=entry["sku"],
        product_id=entry["id"] if entry["kind"] == "product" else None,
        service_id=entry["id"] if entry["kind"] == "service" else None,
        quantity=req.quantity,
        unit_price_usd=Decimal(price),
        match_method=method,  # type: ignore[arg-type]
        source_description=req.description,
    )


def resolve_line_item(
    db: Session,
    req: RequestedLineItem,
    catalog: list[dict[str, Any]] | None = None,
) -> ResolvedLineItem | None:
    """Attempt exact-then-fuzzy resolution. Returns None if no match ≥ threshold.

    Callers that want an LLM fallback should chain to `resolve_via_llm`
    after None is returned here.

    Side effects: loads the catalog on first call if not supplied.
    """
    if catalog is None:
        catalog = _load_catalog(db)

    # 1. Exact SKU
    if req.mentioned_sku:
        target = req.mentioned_sku.strip().upper()
        for entry in catalog:
            if entry["sku"].upper() == target:
                return _build_resolved(entry, req, "exact_sku")

    # 2. Fuzzy name
    names = [e["name"] for e in catalog]
    best = process.extractOne(
        req.description,
        names,
        scorer=fuzz.WRatio,
    )
    if best is not None:
        _, score, idx = best
        if score >= FUZZY_SCORE_MIN:
            return _build_resolved(catalog[idx], req, "fuzzy_name")

    return None
