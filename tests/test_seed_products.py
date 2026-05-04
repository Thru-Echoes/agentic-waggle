"""E2E tests for the products generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import products as products_gen


class TestGenerate:
    def test_exactly_sixty_skus(self, faker_instance) -> None:
        ps = products_gen.generate(faker_instance)
        assert len(ps) == 60

    def test_all_skus_unique(self, faker_instance) -> None:
        ps = products_gen.generate(faker_instance)
        skus = [p.sku for p in ps]
        assert len(skus) == len(set(skus))

    def test_prices_are_positive(self, faker_instance) -> None:
        for p in products_gen.generate(faker_instance):
            assert p.price_usd > Decimal("0")

    def test_category_distribution(self, faker_instance) -> None:
        by_cat = Counter(p.category for p in products_gen.generate(faker_instance))
        # Hand-tuned catalog composition — adjust if catalog grows.
        assert by_cat["honey"] == 20
        assert by_cat["queens_and_bees"] == 15
        assert by_cat["equipment"] == 15
        assert by_cat["beeswax_products"] == 5
        assert by_cat["consumables"] == 3
        assert by_cat["treatments"] == 2

    def test_regulated_flag_set_on_treatments(self, faker_instance) -> None:
        for p in products_gen.generate(faker_instance):
            if p.category == "treatments":
                assert p.is_regulated, f"treatment {p.sku!r} should be regulated"


class TestPersist:
    def test_inserts_all(self, db, faker_instance) -> None:
        products_gen.persist(db, products_gen.generate(faker_instance))
        assert db.execute(text("SELECT COUNT(*) FROM products")).scalar() == 60

    def test_duplicate_sku_raises(self, db, faker_instance) -> None:
        ps = products_gen.generate(faker_instance)
        products_gen.persist(db, ps)
        with pytest.raises(IntegrityError):
            products_gen.persist(db, ps)
