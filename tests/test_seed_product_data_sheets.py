"""E2E tests for the product-data-sheets seed module.

Real DB, real fixture file. Tests the contract:
  * persist creates one `documents` row per fixture entry, source_type='product_data_sheet'
  * each doc resolves to a real product (`source_id` references `products.id`)
  * metadata carries SKU + category + regulated flag for hybrid retrieval
  * fail-loud on missing fixture or unknown SKU
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from seed import apiarists as apiarists_gen
from seed import customers as customers_gen
from seed import product_data_sheets as ps_gen
from seed import products as products_gen
from seed import regions as regions_gen
from seed import sites as sites_gen


def _setup_deps(db, faker_instance) -> None:
    """Persist enough rows that product_data_sheets.persist can resolve SKUs."""
    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    products = products_gen.generate(faker_instance)
    products_gen.persist(db, products)


class TestPersist:
    def test_persists_every_fixture_entry(self, db, faker_instance) -> None:
        _setup_deps(db, faker_instance)
        n = ps_gen.persist(db)
        assert n > 0, "fixture should have at least one product data sheet"
        rows = db.execute(
            text(
                "SELECT count(*) FROM documents "
                "WHERE source_type = 'product_data_sheet'"
            )
        ).scalar()
        assert rows == n, f"persist returned {n} but DB has {rows} rows"

    def test_source_id_points_at_real_product(self, db, faker_instance) -> None:
        _setup_deps(db, faker_instance)
        ps_gen.persist(db)
        # Every product_data_sheet doc's source_id must be in `products`.
        orphans = db.execute(
            text(
                "SELECT count(*) FROM documents d "
                "LEFT JOIN products p ON p.id = d.source_id "
                "WHERE d.source_type = 'product_data_sheet' AND p.id IS NULL"
            )
        ).scalar()
        assert orphans == 0, (
            f"{orphans} product-data-sheet docs reference unknown product ids"
        )

    def test_metadata_includes_sku_category_and_regulated_flag(
        self, db, faker_instance
    ) -> None:
        _setup_deps(db, faker_instance)
        ps_gen.persist(db)
        rows = db.execute(
            text(
                "SELECT metadata FROM documents "
                "WHERE source_type = 'product_data_sheet'"
            )
        ).all()
        assert rows, "no product_data_sheet documents persisted"
        for (meta,) in rows:
            for key in ("product_sku", "product_name", "category", "is_regulated"):
                assert key in meta, f"metadata missing {key!r}: {meta}"
            assert meta["product_sku"], "empty product_sku"
            assert isinstance(meta["is_regulated"], bool), (
                f"is_regulated should be bool: {meta}"
            )

    def test_content_is_substantive(self, db, faker_instance) -> None:
        """Data sheets aren't placeholder strings — they're real authored docs."""
        _setup_deps(db, faker_instance)
        ps_gen.persist(db)
        rows = db.execute(
            text(
                "SELECT length(content) FROM documents "
                "WHERE source_type = 'product_data_sheet'"
            )
        ).scalars().all()
        assert all(n >= 800 for n in rows), (
            f"data sheets are too short to be useful: {sorted(rows)[:3]}"
        )

    def test_unknown_sku_raises_lookup_error(
        self, db, faker_instance, tmp_path, monkeypatch
    ) -> None:
        """Fail-loud: a fixture entry referencing a missing SKU must raise."""
        _setup_deps(db, faker_instance)
        bogus_fixture = tmp_path / "ps.json"
        bogus_fixture.write_text(
            json.dumps(
                [
                    {
                        "product_sku": "DOES-NOT-EXIST-XYZ",
                        "product_name": "Phantom",
                        "category": "honey",
                        "is_regulated": False,
                        "content": "x" * 1000,
                    }
                ]
            )
        )
        monkeypatch.setattr(ps_gen, "FIXTURE_PATH", bogus_fixture)
        with pytest.raises(LookupError, match="unknown SKU"):
            ps_gen.persist(db)

    def test_missing_fixture_raises_loudly(
        self, db, faker_instance, tmp_path, monkeypatch
    ) -> None:
        _setup_deps(db, faker_instance)
        nonexistent = tmp_path / "absent.json"
        monkeypatch.setattr(ps_gen, "FIXTURE_PATH", nonexistent)
        with pytest.raises(ps_gen.FixtureMissing):
            ps_gen.persist(db)
