"""E2E tests for the synthetic email generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import apiarists as apiarists_gen
from seed import customers as customers_gen
from seed import emails as emails_gen
from seed import regions as regions_gen


class TestGenerate:
    def test_exactly_eighty_emails(self, faker_instance) -> None:
        # 45 clear + 20 ambiguous + 8 non-order + 7 complex = 80.
        # Must stay at least 65 to satisfy the orders-seed email-consuming
        # states (EXTRACTED 20 + UNDER_REVIEW 18 + APPROVED 15 + SCHEDULED 12).
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        msgs = emails_gen.generate(faker_instance, customers, apiarists)
        assert len(msgs) == 80

    def test_external_ids_unique(self, faker_instance) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        ids = [m.external_id for m in emails_gen.generate(faker_instance, customers, apiarists)]
        assert len(ids) == len(set(ids))

    def test_subjects_are_non_empty(self, faker_instance) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        for m in emails_gen.generate(faker_instance, customers, apiarists):
            assert m.subject.strip()

    def test_account_ids_are_apiarist_emails(self, faker_instance) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        apiarist_emails = {a.email for a in apiarists}
        for m in emails_gen.generate(faker_instance, customers, apiarists):
            assert m.account_id in apiarist_emails


class TestPersist:
    def test_inserts_and_writes_fixtures(self, db, faker_instance, inbox_fixture_root) -> None:
        import json

        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        import seed.emails as emails_mod
        emails_mod.GMAIL_FIXTURE_ROOT = inbox_fixture_root
        msgs = emails_gen.generate(faker_instance, customers, apiarists)
        emails_gen.persist(db, msgs)

        # DB side
        assert db.execute(text("SELECT COUNT(*) FROM synced_emails")).scalar() == 80

        # Fixture side — verify actual JSON structure, not just presence.
        fixtures = list(inbox_fixture_root.glob("*.inbox.json"))
        assert len(fixtures) > 0, "no gmail fixtures written"
        total_fixture_rows = 0
        required_keys = {
            "id", "thread_id", "account_id", "from_address",
            "to_addresses", "subject", "body_text", "received_at",
        }
        for path in fixtures:
            data = json.loads(path.read_text())
            assert isinstance(data, list), f"fixture {path} is not a JSON array"
            for row in data:
                missing = required_keys - row.keys()
                assert not missing, f"fixture row missing keys {missing}: {row}"
                assert row["id"], "fixture row has empty id"
                assert row["body_text"], "fixture row has empty body"
                total_fixture_rows += 1
        # Every generated email should be in some inbox fixture.
        assert total_fixture_rows == 80

    def test_duplicate_external_id_raises(self, db, faker_instance, inbox_fixture_root) -> None:
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        import seed.emails as emails_mod
        emails_mod.GMAIL_FIXTURE_ROOT = inbox_fixture_root
        msgs = emails_gen.generate(faker_instance, customers, apiarists)
        emails_gen.persist(db, msgs)
        with pytest.raises(IntegrityError):
            emails_gen.persist(db, msgs)
