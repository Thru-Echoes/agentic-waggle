"""Schema contract for the semantic-matcher tables introduced by migration 004.

``customer_search_entries`` and ``sku_search_entries`` carry a pgvector
column, a generated ``tsvector`` column, an ivfflat index, and two CHECK
constraints. These tests pin that shape against ``tests/schema.sql`` (the
per-test fresh schema) and prove the constraints reject bad rows.

Each test uses the ``db`` fixture (fresh schema per test) and seeds only what
it needs, so it never depends on rows left behind by another module.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

_PLACEHOLDER_EMBEDDING = "[" + ",".join(["0.0"] * 384) + "]"


def _seed_customers_products_services(db: Session, faker_instance) -> None:
    """Persist regions, customers, sites, products, services for FK targets."""
    from seed import customers as customers_gen
    from seed import products as products_gen
    from seed import regions as regions_gen
    from seed import services as services_gen
    from seed import sites as sites_gen

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites_gen.persist(db, sites_gen.generate(faker_instance, customers, regions))
    products_gen.persist(db, products_gen.generate(faker_instance))
    services_gen.persist(db, services_gen.generate())
    db.commit()


def _column_types(db: Session, table: str) -> dict[str, str]:
    rows = db.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = :t ORDER BY ordinal_position"
        ),
        {"t": table},
    ).fetchall()
    return {row.column_name: row.data_type for row in rows}


def _constraint_def(db: Session, name: str) -> str:
    return db.execute(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
        {"n": name},
    ).scalar_one()


def _index_def(db: Session, table: str, needle: str) -> str | None:
    rows = db.execute(
        text("SELECT indexdef FROM pg_indexes WHERE tablename = :t"),
        {"t": table},
    ).scalars().all()
    return next((d for d in rows if needle in d), None)


def test_customer_search_entries_columns(db: Session) -> None:
    cols = _column_types(db, "customer_search_entries")
    assert cols["id"] == "uuid"
    assert cols["customer_id"] == "uuid"
    assert cols["entry_text"] == "text"
    assert cols["source"] == "text"
    assert cols["embedding"] == "USER-DEFINED"  # vector(384)
    assert cols["tsv"] == "tsvector"


def test_customer_search_entries_source_check_constraint(db: Session) -> None:
    definition = _constraint_def(db, "customer_search_entries_source_check")
    for allowed in ("'name'", "'alias'", "'site'"):
        assert allowed in definition


def test_sku_search_entries_exactly_one_target_constraint(db: Session) -> None:
    """product_id XOR service_id: never both, never neither."""
    definition = _constraint_def(db, "sku_search_entries_exactly_one_target")
    assert "product_id IS NULL" in definition
    assert "service_id IS NULL" in definition


def test_customer_search_entries_pgvector_index_exists(db: Session) -> None:
    assert _index_def(db, "customer_search_entries", "ivfflat") is not None


def test_sku_search_entries_pgvector_index_exists(db: Session) -> None:
    assert _index_def(db, "sku_search_entries", "ivfflat") is not None


def test_customer_search_entries_rejects_invalid_source(
    db: Session, faker_instance
) -> None:
    """INSERT with source='not_a_valid_source' must raise IntegrityError."""
    _seed_customers_products_services(db, faker_instance)
    customer_id = db.execute(text("SELECT id FROM customers LIMIT 1")).scalar_one()
    with pytest.raises(IntegrityError, match="customer_search_entries_source_check"):
        db.execute(
            text(
                "INSERT INTO customer_search_entries "
                "(customer_id, entry_text, source, embedding) "
                "VALUES (:cid, :text, :src, :emb)"
            ),
            {
                "cid": customer_id,
                "text": "bad source test",
                "src": "not_a_valid_source",
                "emb": _PLACEHOLDER_EMBEDDING,
            },
        )
    db.rollback()


def test_sku_search_entries_rejects_both_fks_set(db: Session, faker_instance) -> None:
    """INSERT with both product_id and service_id non-null must raise IntegrityError."""
    _seed_customers_products_services(db, faker_instance)
    product_id = db.execute(text("SELECT id FROM products LIMIT 1")).scalar_one()
    service_id = db.execute(text("SELECT id FROM services LIMIT 1")).scalar_one()
    with pytest.raises(IntegrityError, match="sku_search_entries_exactly_one_target"):
        db.execute(
            text(
                "INSERT INTO sku_search_entries "
                "(product_id, service_id, entry_text, source, embedding) "
                "VALUES (:pid, :sid, :text, :src, :emb)"
            ),
            {
                "pid": product_id,
                "sid": service_id,
                "text": "both set test",
                "src": "product_name",
                "emb": _PLACEHOLDER_EMBEDDING,
            },
        )
    db.rollback()


def test_sku_search_entries_rejects_neither_fk_set(db: Session) -> None:
    """INSERT with both FKs NULL must also fail the XOR constraint."""
    with pytest.raises(IntegrityError, match="sku_search_entries_exactly_one_target"):
        db.execute(
            text(
                "INSERT INTO sku_search_entries "
                "(product_id, service_id, entry_text, source, embedding) "
                "VALUES (NULL, NULL, :text, :src, :emb)"
            ),
            {"text": "neither set", "src": "product_name", "emb": _PLACEHOLDER_EMBEDDING},
        )
    db.rollback()
