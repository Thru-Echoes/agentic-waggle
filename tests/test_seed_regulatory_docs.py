"""E2E tests for the regulatory-docs seed module.

These docs have no parent domain table — they're standalone regulatory
references. Tests cover the persist path + the curated-list invariants
(distinct codes, every regime represented, Tropilaelaps protocol present
because it's a portfolio-distinguishing regulatory-grade scenario).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text

from seed import regulatory_docs as rd_gen


class TestPersist:
    def test_persists_every_fixture_entry(self, db) -> None:
        n = rd_gen.persist(db)
        assert n > 0, "regulatory fixture should have at least one entry"
        rows = db.execute(
            text(
                "SELECT count(*) FROM documents "
                "WHERE source_type = 'regulatory_doc'"
            )
        ).scalar()
        assert rows == n

    def test_metadata_carries_code_title_scope(self, db) -> None:
        rd_gen.persist(db)
        rows = db.execute(
            text(
                "SELECT metadata FROM documents "
                "WHERE source_type = 'regulatory_doc'"
            )
        ).all()
        assert rows, "no regulatory docs persisted"
        for (meta,) in rows:
            for key in ("code", "title", "scope"):
                assert key in meta, f"metadata missing {key!r}: {meta}"
            assert meta["scope"] in {"federal", "state-CA"}, (
                f"unexpected scope value: {meta['scope']!r}"
            )

    def test_tropilaelaps_protocol_present(self, db) -> None:
        """The USDA-APHIS Tropilaelaps quarantine protocol is a regulatory-
        grade scenario the system advertises. If it's missing, the demo's
        federal-quarantine answer falls back to no-data."""
        rd_gen.persist(db)
        row = db.execute(
            text(
                "SELECT metadata->>'code' FROM documents "
                "WHERE source_type = 'regulatory_doc' "
                "AND metadata->>'code' LIKE '%TROPILAELAPS%'"
            )
        ).first()
        assert row is not None, (
            "USDA-APHIS Tropilaelaps regulatory doc missing from fixture"
        )

    def test_codes_are_distinct(self, db) -> None:
        """Curated list — duplicate code suggests a fixture authoring error."""
        rd_gen.persist(db)
        codes = db.execute(
            text(
                "SELECT metadata->>'code' FROM documents "
                "WHERE source_type = 'regulatory_doc'"
            )
        ).scalars().all()
        assert len(codes) == len(set(codes)), (
            f"duplicate codes in regulatory fixture: {codes}"
        )

    def test_each_doc_substantive(self, db) -> None:
        rd_gen.persist(db)
        lengths = db.execute(
            text(
                "SELECT length(content) FROM documents "
                "WHERE source_type = 'regulatory_doc'"
            )
        ).scalars().all()
        assert all(n >= 800 for n in lengths), (
            f"regulatory docs too short to be useful: {sorted(lengths)}"
        )

    def test_missing_fixture_raises_loudly(
        self, db, tmp_path, monkeypatch
    ) -> None:
        nonexistent = tmp_path / "absent.json"
        monkeypatch.setattr(rd_gen, "FIXTURE_PATH", nonexistent)
        with pytest.raises(rd_gen.FixtureMissing):
            rd_gen.persist(db)
