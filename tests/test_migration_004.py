"""Migration 004 creates customer_search_entries + sku_search_entries
with pgvector + FTS indexes and the right CHECK constraints.
"""
from __future__ import annotations

from sqlalchemy import text

from db.session import session_scope


def test_customer_search_entries_table_exists():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'customer_search_entries' ORDER BY ordinal_position"
            )
        ).fetchall()
    cols = {row.column_name: row.data_type for row in result}
    assert cols["id"] == "uuid"
    assert cols["customer_id"] == "uuid"
    assert cols["entry_text"] == "text"
    assert cols["source"] == "text"
    assert cols["embedding"] == "USER-DEFINED"  # vector(384)
    assert cols["tsv"] == "tsvector"


def test_customer_search_entries_source_check_constraint():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'customer_search_entries_source_check'"
            )
        ).scalar_one()
    assert "'name'" in result
    assert "'alias'" in result
    assert "'site'" in result


def test_sku_search_entries_exactly_one_target_constraint():
    """product_id XOR service_id — never both, never neither."""
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'sku_search_entries_exactly_one_target'"
            )
        ).scalar_one()
    # Expect: CHECK ((product_id IS NULL) <> (service_id IS NULL))
    assert "product_id IS NULL" in result
    assert "service_id IS NULL" in result


def test_customer_search_entries_pgvector_index_exists():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'customer_search_entries_embedding_ivfflat'"
            )
        ).scalar_one()
    assert "ivfflat" in result
    assert "vector_cosine_ops" in result


def test_customer_search_entries_fts_index_exists():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'customer_search_entries_tsv_gin'"
            )
        ).scalar_one()
    assert "gin" in result
    assert "tsv" in result
