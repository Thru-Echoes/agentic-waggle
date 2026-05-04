"""E2E tests for the sites generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text

from seed import customers as customers_gen
from seed import regions as regions_gen
from seed import sites as sites_gen


class TestGenerate:
    def test_every_customer_has_at_least_one_site(self, faker_instance) -> None:
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions_gen.generate())
        customers_with_sites = {s.customer_name for s in sites}
        for c in customers:
            assert c.name in customers_with_sites

    def test_orchards_have_multiple_sites(self, faker_instance) -> None:
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions_gen.generate())
        orchards = [c for c in customers if c.market_segment.startswith("ORCHARD_")]
        for c in orchards:
            n = sum(1 for s in sites if s.customer_name == c.name)
            assert n >= 2, f"orchard {c.name!r} has only {n} site(s)"

    def test_region_codes_all_valid(self, faker_instance) -> None:
        regions = regions_gen.generate()
        valid_codes = {r.code for r in regions}
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions)
        for s in sites:
            assert s.region_code in valid_codes

    def test_acreage_present_for_orchard_sites(self, faker_instance) -> None:
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions_gen.generate())
        for s in sites:
            customer = next(c for c in customers if c.name == s.customer_name)
            if customer.market_segment.startswith("ORCHARD_"):
                assert s.acreage_by_crop, f"orchard site {s.name!r} has no acreage"


class TestPersist:
    def test_inserts_all_sites(self, db, faker_instance) -> None:
        regions_gen.persist(db, regions_gen.generate())
        customers = customers_gen.generate(faker_instance)
        customers_gen.persist(db, customers)
        sites = sites_gen.generate(faker_instance, customers, regions_gen.generate())
        sites_gen.persist(db, sites)
        assert db.execute(text("SELECT COUNT(*) FROM customer_sites")).scalar() == len(sites)

    def test_missing_customer_raises_lookup_error(self, db, faker_instance) -> None:
        regions_gen.persist(db, regions_gen.generate())
        # Do NOT persist customers — sites.persist should fail loudly.
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions_gen.generate())
        with pytest.raises(LookupError):
            sites_gen.persist(db, sites)
