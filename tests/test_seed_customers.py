"""E2E tests for the customers generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import customers as customers_gen
from seed.faker_providers import EDGE_CASE_CUSTOMERS


class TestGenerate:
    def test_exactly_forty(self, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        assert len(cs) == 40

    def test_market_segment_distribution_matches_targets(self, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        by_segment = Counter(c.market_segment for c in cs)
        for segment, target in customers_gen.SEGMENT_TARGETS.items():
            assert by_segment[segment] == target, (
                f"segment {segment}: expected {target}, got {by_segment[segment]}"
            )

    def test_all_edge_case_names_present(self, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        names = {c.name for c in cs}
        for canonical, _ in EDGE_CASE_CUSTOMERS:
            assert canonical in names, f"missing edge-case customer {canonical!r}"

    def test_edge_case_aliases_populated(self, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        oreilly = next(c for c in cs if c.name == "O'Reilly Family Orchards")
        assert len(oreilly.aliases) >= 3

    def test_erp_numbers_unique(self, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        nos = [c.erp_customer_no for c in cs]
        assert len(nos) == len(set(nos))

    def test_deterministic_with_fixed_seed(self) -> None:
        from faker import Faker
        from seed.faker_providers import BeekeepingProvider

        def make() -> list:
            f = Faker(locale="en_US")
            Faker.seed(42)
            f.add_provider(BeekeepingProvider)
            return [c.name for c in customers_gen.generate(f)]

        assert make() == make()


class TestPersist:
    def test_inserts_all(self, db, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        customers_gen.persist(db, cs)
        assert db.execute(text("SELECT COUNT(*) FROM customers")).scalar() == 40

    def test_aliases_stored_as_jsonb(self, db, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        customers_gen.persist(db, cs)
        row = db.execute(
            text("SELECT aliases FROM customers WHERE name = :n"),
            {"n": "O'Reilly Family Orchards"},
        ).first()
        assert row is not None
        assert "O'Reilly's Almonds" in row[0]

    def test_duplicate_erp_no_raises(self, db, faker_instance) -> None:
        cs = customers_gen.generate(faker_instance)
        customers_gen.persist(db, cs)
        with pytest.raises(IntegrityError):
            customers_gen.persist(db, cs)
