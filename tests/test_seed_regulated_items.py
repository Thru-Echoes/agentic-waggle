"""E2E tests for regulated_items — with explicit count (no padding)."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text

from seed import products as products_gen
from seed import regulated_items as regulated_items_gen


class TestGenerate:
    def test_exact_entry_count_matches_hand_curated_list(self, faker_instance) -> None:
        products = products_gen.generate(faker_instance)
        items = regulated_items_gen.generate(products)
        # The count must match the hand-curated ENTRIES list. No padding.
        assert len(items) == len(regulated_items_gen.ENTRIES)

    def test_every_named_sku_resolves_to_real_product(self, faker_instance) -> None:
        products = products_gen.generate(faker_instance)
        product_skus = {p.sku for p in products}
        items = regulated_items_gen.generate(products)
        for item in items:
            if item.product_sku:
                assert item.product_sku in product_skus

    def test_queens_flagged_as_state_import_permit(self, faker_instance) -> None:
        products = products_gen.generate(faker_instance)
        items = regulated_items_gen.generate(products)
        for item in items:
            if item.product_sku and item.product_sku.startswith("QUEEN-"):
                assert item.restriction_type == "state_import_permit"

    def test_unknown_product_sku_in_entries_raises_lookup_error(self) -> None:
        """Verifies the drift-detection behavior between products.py and regulated_items.py."""
        from seed.models import Product
        bogus_products = [
            Product(sku="NOT-IN-LIST", category="honey", name="x", price_usd=1)  # type: ignore[arg-type]
        ]
        with pytest.raises(LookupError):
            regulated_items_gen.generate(bogus_products)


class TestPersist:
    def test_inserts_all(self, db, faker_instance) -> None:
        products = products_gen.generate(faker_instance)
        products_gen.persist(db, products)
        items = regulated_items_gen.generate(products)
        regulated_items_gen.persist(db, items)
        count = db.execute(text("SELECT COUNT(*) FROM regulated_items")).scalar()
        assert count == len(items)

    def test_persist_fails_if_products_not_seeded(self, db, faker_instance) -> None:
        products = products_gen.generate(faker_instance)
        items = regulated_items_gen.generate(products)
        # Skip products.persist — regulated items reference non-existent SKUs.
        with pytest.raises(LookupError):
            regulated_items_gen.persist(db, items)
