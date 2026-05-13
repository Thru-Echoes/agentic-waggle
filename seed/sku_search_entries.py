"""Backfill generator for sku_search_entries.

backfill_all() in providers.semantic_matcher already handles both
customer and SKU tables. This module exists for parity with seed/
naming conventions and as the future split point if the backfills
are decoupled.

Public exports:
    persist(db) -> int

Side effects: none (no-op; backfill is handled by customer_search_entries.persist).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from sqlalchemy.orm import Session


def persist(db: Session) -> int:
    """No-op — backfill is handled by customer_search_entries.persist.

    Parameters:
        db: SQLAlchemy Session (unused; accepted for API parity).

    Returns:
        0 (no rows written by this module directly).

    Side effects: none.
    """
    return 0
