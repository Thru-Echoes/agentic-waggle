"""E2E tests for the regions generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import regions as regions_gen


class TestGenerate:
    def test_exactly_six_regions(self) -> None:
        rs = regions_gen.generate()
        assert len(rs) == 6

    def test_codes_are_unique(self) -> None:
        rs = regions_gen.generate()
        codes = [r.code for r in rs]
        assert len(codes) == len(set(codes))

    def test_peak_months_are_valid(self) -> None:
        for r in regions_gen.generate():
            assert 1 <= r.peak_start_month <= 12
            assert 1 <= r.peak_end_month <= 12

    def test_central_valley_peaks_february(self) -> None:
        """Domain sanity: almond bloom is February."""
        r = next(r for r in regions_gen.generate() if r.code == "central_valley")
        assert r.peak_start_month == 2
        assert "almond" in r.primary_crops


class TestPersist:
    def test_all_rows_inserted(self, db) -> None:
        regions_gen.persist(db, regions_gen.generate())
        count = db.execute(text("SELECT COUNT(*) FROM territories")).scalar()
        assert count == 6

    def test_codes_stored(self, db) -> None:
        regions_gen.persist(db, regions_gen.generate())
        rows = db.execute(text("SELECT code FROM territories ORDER BY code")).all()
        codes = [r[0] for r in rows]
        assert set(codes) == {
            "central_valley", "midwest", "mountain_west",
            "northeast", "pacific_northwest", "southeast",
        }

    def test_metadata_preserves_primary_crops(self, db) -> None:
        regions_gen.persist(db, regions_gen.generate())
        row = db.execute(
            text("SELECT metadata FROM territories WHERE code = 'central_valley'")
        ).first()
        assert row is not None
        assert "almond" in row[0]["primary_crops"]

    def test_duplicate_insertion_fails_loudly(self, db) -> None:
        regions_gen.persist(db, regions_gen.generate())
        with pytest.raises(IntegrityError):
            regions_gen.persist(db, regions_gen.generate())
