"""boot.py runs reindex_if_empty on every deploy. After migration 004
applies, tables exist but are empty; boot.py must call backfill_all so
the live app never hits MatcherIndexEmpty."""
from __future__ import annotations

from sqlalchemy import text


def test_reindex_if_empty_populates_on_empty_table(db_session_with_seed):
    from scripts import boot
    db = db_session_with_seed
    db.execute(text("TRUNCATE customer_search_entries"))
    db.execute(text("TRUNCATE sku_search_entries"))
    db.commit()
    boot.reindex_if_empty(db)
    n = db.execute(text("SELECT COUNT(*) FROM customer_search_entries")).scalar_one()
    assert n > 0


def test_reindex_if_empty_is_noop_when_populated(db_session_with_seed):
    from scripts import boot
    from providers import semantic_matcher
    db = db_session_with_seed
    semantic_matcher.backfill_all(db)
    before = db.execute(text("SELECT COUNT(*) FROM customer_search_entries")).scalar_one()
    boot.reindex_if_empty(db)
    after = db.execute(text("SELECT COUNT(*) FROM customer_search_entries")).scalar_one()
    assert before == after
