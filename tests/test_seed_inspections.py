"""E2E tests for apiary inspection report generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from seed import apiarists as apiarists_gen
from seed import customers as customers_gen
from seed import inspections as inspections_gen
from seed import regions as regions_gen
from seed import sites as sites_gen


class TestGenerate:
    def test_exactly_forty_reports(self, faker_instance) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        reports = inspections_gen.generate(faker_instance, sites, apiarists)
        assert len(reports) == 40

    def test_narratives_non_empty(self, faker_instance) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        for r in inspections_gen.generate(faker_instance, sites, apiarists):
            assert r.narrative
            assert len(r.narrative) > 50, f"narrative too short: {r.narrative!r}"

    def test_varroa_counts_are_plausible(self, faker_instance) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        for r in inspections_gen.generate(faker_instance, sites, apiarists):
            # Plausible range for real inspections: 0–20 mites/100 bees.
            assert Decimal("0") <= r.varroa_count <= Decimal("20")

    def test_authors_are_field_apiarists(self, faker_instance) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        sites = sites_gen.generate(faker_instance, customers, regions)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        field_emails = {a.email for a in apiarists if a.role == "apiarist"}
        for r in inspections_gen.generate(faker_instance, sites, apiarists):
            assert r.author_email in field_emails


class TestPersist:
    def _setup_deps(self, db, faker_instance):
        regions = regions_gen.generate()
        regions_gen.persist(db, regions)
        customers = customers_gen.generate(faker_instance)
        customers_gen.persist(db, customers)
        sites = sites_gen.generate(faker_instance, customers, regions)
        sites_gen.persist(db, sites)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        apiarists_gen.persist(db, apiarists)
        return sites, apiarists

    def test_inserts_all(self, db, faker_instance) -> None:
        sites, apiarists = self._setup_deps(db, faker_instance)
        reports = inspections_gen.generate(faker_instance, sites, apiarists)
        inspections_gen.persist(db, reports)
        assert db.execute(text("SELECT COUNT(*) FROM customer_reports")).scalar() == 40

    def test_creates_parallel_documents(self, db, faker_instance) -> None:
        sites, apiarists = self._setup_deps(db, faker_instance)
        reports = inspections_gen.generate(faker_instance, sites, apiarists)
        inspections_gen.persist(db, reports)
        assert db.execute(text("SELECT COUNT(*) FROM documents")).scalar() == 40

    def test_missing_site_raises_lookup_error(self, db, faker_instance) -> None:
        # Skip sites.persist — inspections should fail loudly.
        regions = regions_gen.generate()
        regions_gen.persist(db, regions)
        customers = customers_gen.generate(faker_instance)
        customers_gen.persist(db, customers)
        sites = sites_gen.generate(faker_instance, customers, regions)
        # sites NOT persisted
        apiarists = apiarists_gen.generate(faker_instance, regions)
        apiarists_gen.persist(db, apiarists)
        reports = inspections_gen.generate(faker_instance, sites, apiarists)
        with pytest.raises(LookupError):
            inspections_gen.persist(db, reports)
