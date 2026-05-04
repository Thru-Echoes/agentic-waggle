"""E2E tests for the services generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import services as services_gen


class TestGenerate:
    def test_four_services(self) -> None:
        assert len(services_gen.generate()) == 4

    def test_all_skus_unique(self) -> None:
        skus = [s.sku for s in services_gen.generate()]
        assert len(skus) == len(set(skus))

    def test_almond_premium_is_per_hive_flat(self) -> None:
        almond = next(s for s in services_gen.generate() if s.sku == "POL_ALMOND_PREMIUM")
        assert almond.pricing_model == "per_hive_flat"

    def test_custom_pollination_is_negotiated(self) -> None:
        custom = next(s for s in services_gen.generate() if s.sku == "POL_CUSTOM")
        assert custom.pricing_model == "negotiated"
        assert custom.default_rate_usd is None


class TestPersist:
    def test_inserts_all(self, db) -> None:
        services_gen.persist(db, services_gen.generate())
        assert db.execute(text("SELECT COUNT(*) FROM services")).scalar() == 4

    def test_duplicate_sku_raises(self, db) -> None:
        services_gen.persist(db, services_gen.generate())
        with pytest.raises(IntegrityError):
            services_gen.persist(db, services_gen.generate())
