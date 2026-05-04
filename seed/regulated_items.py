"""Regulated-item entries (hazmat analog).

One entry per real regulated product + miticide + VFD category. No
padding to hit a round number — the count is exactly what the rules
dictate, and `test_seed_regulated_items.py` asserts the exact set.

Fail-loud: persist raises if a referenced product SKU is not in the
seeded products, because that means the products generator is out of
sync with this list.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Product, RegulatedItem


@dataclass(frozen=True)
class _RegulatedEntry:
    product_sku: str | None  # None = category-level rule without a concrete SKU
    restriction_type: Literal[
        "state_import_permit", "vfd_required", "epa_label_restriction"
    ]
    restricted_states: tuple[str, ...]
    notes: str


# Quarantine states by restriction class
QUEENS_RESTRICTED = ("CA", "FL", "HI", "AZ")
PACKAGES_RESTRICTED = ("CA", "HI", "WA", "FL")


ENTRIES: list[_RegulatedEntry] = [
    # Queen + package state-import permits (one entry per SKU we ship)
    _RegulatedEntry(
        "QUEEN-ITA-MATED", "state_import_permit", QUEENS_RESTRICTED,
        "State health certificate required; USDA Honey Bee Act permit",
    ),
    _RegulatedEntry(
        "QUEEN-CAR-MATED", "state_import_permit", QUEENS_RESTRICTED,
        "State health certificate required; USDA Honey Bee Act permit",
    ),
    _RegulatedEntry(
        "QUEEN-RUS-MATED", "state_import_permit", QUEENS_RESTRICTED,
        "State health certificate required; USDA Honey Bee Act permit",
    ),
    _RegulatedEntry(
        "QUEEN-SAS-MATED", "state_import_permit", QUEENS_RESTRICTED,
        "State health certificate required; USDA Honey Bee Act permit",
    ),
    _RegulatedEntry(
        "QUEEN-VSH-MATED", "state_import_permit", QUEENS_RESTRICTED,
        "VSH lineage verification required; USDA HBA permit",
    ),
    _RegulatedEntry(
        "QUEEN-BKF-MATED", "state_import_permit", QUEENS_RESTRICTED,
        "State health certificate required; USDA Honey Bee Act permit",
    ),
    _RegulatedEntry(
        "QUEEN-ITA-VIRG", "state_import_permit", QUEENS_RESTRICTED,
        "Virgin queens restricted by some destination states",
    ),
    _RegulatedEntry(
        "PKG-BEES-3LB-ITA", "state_import_permit", PACKAGES_RESTRICTED,
        "State import permit (small hive beetle quarantine)",
    ),
    _RegulatedEntry(
        "PKG-BEES-5LB-ITA", "state_import_permit", PACKAGES_RESTRICTED,
        "State import permit (small hive beetle quarantine)",
    ),
    # EPA-registered miticides
    _RegulatedEntry(
        "TRT-OXALIC-R", "epa_label_restriction", (),
        "EPA-registered oxalic acid; application-window restrictions",
    ),
    _RegulatedEntry(
        "TRT-FORMIC-R", "epa_label_restriction", (),
        "EPA-registered formic acid; ambient-temperature restrictions",
    ),
    # VFD antibiotic — category-level rule (no concrete SKU sold; requires VFD)
    _RegulatedEntry(
        None, "vfd_required", (),
        "Oxytetracycline for AFB control requires Veterinary Feed Directive",
    ),
    _RegulatedEntry(
        None, "vfd_required", (),
        "Lincomycin for AFB control requires VFD",
    ),
    _RegulatedEntry(
        None, "vfd_required", (),
        "Tylosin for EFB control requires VFD",
    ),
]


def generate(products: list[Product]) -> list[RegulatedItem]:
    """Return one RegulatedItem per entry. Fails if a named product SKU
    is absent from the products list — this means the products generator
    drifted from the regulated-items list and must be reconciled."""
    skus_available = {p.sku for p in products}
    out: list[RegulatedItem] = []
    for entry in ENTRIES:
        if entry.product_sku is not None and entry.product_sku not in skus_available:
            raise LookupError(
                f"Regulated-item entry references SKU {entry.product_sku!r} "
                f"which is not in the seeded products list. "
                f"Either add it to products.py or remove it from regulated_items.py."
            )
        out.append(
            RegulatedItem(
                product_sku=entry.product_sku,  # may be None for category rules
                restriction_type=entry.restriction_type,
                restricted_states=list(entry.restricted_states),
                notes=entry.notes,
            )
        )
    return out


def persist(db: Session, items: list[RegulatedItem]) -> None:
    """Insert into `regulated_items`. Missing SKU raises (when not None)."""
    prows = db.execute(text("SELECT id, sku FROM products")).mappings().all()
    sku_to_id = {r["sku"]: r["id"] for r in prows}

    for item in items:
        product_id: str | None = None
        if item.product_sku is not None:
            if item.product_sku not in sku_to_id:
                raise LookupError(
                    f"RegulatedItem references unknown product SKU "
                    f"{item.product_sku!r}; seed products first."
                )
            product_id = sku_to_id[item.product_sku]

        db.execute(
            text(
                "INSERT INTO regulated_items "
                "(id, product_id, restriction_type, restricted_states, notes) "
                "VALUES (:id, :pid, :rtype, CAST(:states AS jsonb), :notes)"
            ),
            {
                "id": str(uuid.uuid4()),
                "pid": product_id,
                "rtype": item.restriction_type,
                "states": json.dumps(item.restricted_states),
                "notes": item.notes,
            },
        )
