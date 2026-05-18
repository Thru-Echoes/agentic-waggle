"""Backfill generator for customer_search_entries.

Imported by seed/embeddings.py; runs after customers + sites are seeded.

Public exports:
    persist(db) -> int

Side effects: writes via providers.semantic_matcher.backfill_all.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from sqlalchemy.orm import Session


def persist(db: Session) -> int:
    """Backfill customer search entries. Idempotent (TRUNCATE-and-rebuild).

    Parameters:
        db: SQLAlchemy Session connected to a live Postgres with pgvector.

    Returns:
        Total number of rows written across customer_search_entries and
        sku_search_entries (backfill_all covers both tables).

    Side effects: delegates to providers.semantic_matcher.backfill_all,
        which TRUNCATEs and re-INSERTs both search-entry tables and commits.
    """
    from providers import semantic_matcher
    return semantic_matcher.backfill_all(db)
