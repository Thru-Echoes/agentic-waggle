"""Full-seed integration test.

Runs the entire `seed.generate.run()` pipeline against a fresh test DB and
asserts the expected row counts across every table. This is the smoke
test that tells you the seed end-to-end works.

Run with:

    TEST_DATABASE_URL=postgresql://...@localhost:5433/test pytest tests/test_integration_full_seed.py -vv
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os

from sqlalchemy import text

from seed import generate as seed_generate


def test_full_seed_produces_expected_counts(db, monkeypatch, inbox_fixture_root) -> None:
    # Point the seed pipeline at the test DB
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    # The emails generator writes fixtures to a module-level path;
    # route them into the test fixture dir so they don't pollute /gmail-fixtures.
    import seed.emails as emails_mod
    monkeypatch.setattr(emails_mod, "GMAIL_FIXTURE_ROOT", inbox_fixture_root)

    # The seed's session_scope creates its own engine from DATABASE_URL.
    # The conftest `db` fixture has already created schema and made the DB
    # ready. Run the full pipeline.
    seed_generate.run(drop_existing=False)

    # documents = 40 customer_reports + 80 emails + 8 product_data_sheets +
    # 5 regulatory_docs = 133. Each source type is independently counted
    # below so a regression in any one is easy to localize.
    expected: dict[str, int] = {
        "territories": 6,
        "users": 16,
        "customers": 40,
        "products": 60,
        "services": 4,
        "red_flag_rules": 10,
        "approval_rules": 10,
        "customer_reports": 40,
        "documents": 133,
        "synced_emails": 80,
        "orders": 150,
        "order_state_transitions": 150,
    }
    for table, expected_n in expected.items():
        actual = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        assert actual == expected_n, f"{table}: got {actual}, want {expected_n}"

    # documents broken down by source_type — multi-source RAG corpus
    doc_types = dict(
        db.execute(
            text(
                "SELECT source_type, COUNT(*) FROM documents "
                "GROUP BY source_type"
            )
        ).all()
    )
    assert doc_types == {
        "customer_report": 40,
        "email": 80,
        "product_data_sheet": 8,
        "regulatory_doc": 5,
    }, f"documents source_type breakdown: {doc_types}"

    # Derived counts (ranges, not exact)
    sites = db.execute(text("SELECT COUNT(*) FROM customer_sites")).scalar() or 0
    assert 40 <= sites <= 160, f"customer_sites out of range: {sites}"

    line_items = db.execute(text("SELECT COUNT(*) FROM order_line_items")).scalar() or 0
    assert line_items >= 150, f"order_line_items too low: {line_items}"

    # The tighter chunker + LLM-authored 1200-1800 char narratives produce
    # multiple chunks per customer-report doc; emails stay 1 chunk each;
    # product sheets and reg docs split into 3-5 chunks. Aim ~250+ total.
    chunks = db.execute(text("SELECT COUNT(*) FROM chunks")).scalar() or 0
    assert chunks >= 200, f"chunks too low: {chunks}"

    # Every source_type produces at least one chunk.
    chunks_by_source = dict(
        db.execute(
            text(
                "SELECT metadata->>'source_type' AS src, COUNT(*) "
                "FROM chunks GROUP BY 1"
            )
        ).all()
    )
    for src in ("customer_report", "email", "product_data_sheet", "regulatory_doc"):
        assert chunks_by_source.get(src, 0) > 0, (
            f"missing chunks for source_type={src!r}: {chunks_by_source}"
        )

    # Embedding dim invariant
    dims = db.execute(text("SELECT DISTINCT vector_dims(embedding) FROM chunks")).scalars().all()
    assert list(dims) == [384]
