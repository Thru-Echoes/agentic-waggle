"""E2E tests for the apiarists generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import apiarists as apiarists_gen
from seed import regions as regions_gen


class TestGenerate:
    def test_sixteen_users(self, faker_instance) -> None:
        us = apiarists_gen.generate(faker_instance, regions_gen.generate())
        assert len(us) == 16

    def test_role_distribution(self, faker_instance) -> None:
        us = apiarists_gen.generate(faker_instance, regions_gen.generate())
        roles = [u.role for u in us]
        assert roles.count("apiarist") == 12
        assert roles.count("ops_manager") == 3
        assert roles.count("admin") == 1

    def test_emails_are_unique(self, faker_instance) -> None:
        us = apiarists_gen.generate(faker_instance, regions_gen.generate())
        emails = [u.email for u in us]
        assert len(emails) == len(set(emails))

    def test_erp_numbers_are_unique(self, faker_instance) -> None:
        us = apiarists_gen.generate(faker_instance, regions_gen.generate())
        nos = [u.erp_apiarist_no for u in us]
        assert len(nos) == len(set(nos))

    def test_unknown_region_code_raises(self, faker_instance) -> None:
        # Monkey-patch the fixed roster to include a bad region.
        from seed import apiarists as ap_mod
        orig = ap_mod.FIELD_APIARISTS
        try:
            ap_mod.FIELD_APIARISTS = [("Broken Person", ["no_such_region"])]
            with pytest.raises(AssertionError):
                ap_mod.generate(faker_instance, regions_gen.generate())
        finally:
            ap_mod.FIELD_APIARISTS = orig


class TestPersist:
    def test_inserts_all_users(self, db, faker_instance) -> None:
        regions_gen.persist(db, regions_gen.generate())
        apiarists = apiarists_gen.generate(faker_instance, regions_gen.generate())
        apiarists_gen.persist(db, apiarists)
        assert db.execute(text("SELECT COUNT(*) FROM users")).scalar() == 16

    def test_creates_territory_assignments(self, db, faker_instance) -> None:
        regions_gen.persist(db, regions_gen.generate())
        apiarists = apiarists_gen.generate(faker_instance, regions_gen.generate())
        apiarists_gen.persist(db, apiarists)
        # Each apiarist has >= 0 regions, so at least some assignments exist.
        n_assign = db.execute(text("SELECT COUNT(*) FROM territory_assignments")).scalar()
        assert n_assign > 0

    def test_missing_region_raises_lookup_error(self, db, faker_instance) -> None:
        # Don't seed regions first; apiarists.persist should fail loudly
        # when it cannot resolve a region code.
        apiarists = apiarists_gen.generate(faker_instance, regions_gen.generate())
        with pytest.raises(LookupError):
            apiarists_gen.persist(db, apiarists)

    def test_duplicate_email_raises_integrity_error(self, db, faker_instance) -> None:
        regions_gen.persist(db, regions_gen.generate())
        apiarists = apiarists_gen.generate(faker_instance, regions_gen.generate())
        apiarists_gen.persist(db, apiarists)
        with pytest.raises(IntegrityError):
            apiarists_gen.persist(db, apiarists)
