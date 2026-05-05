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


class TestStableMessageIds:
    """Email message ids must be deterministic across reseeds so the
    gmail-fixtures/*.inbox.json files don't churn in git on every reset-db."""

    def _gen(self, faker_instance):
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        return emails_gen.generate(faker_instance, customers, apiarists)

    def test_ids_stable_across_reseeds(self, faker_instance) -> None:
        """Re-running generate() with the same fixture must produce the
        same external_ids (content-hashed)."""
        first = [m.external_id for m in self._gen(faker_instance)]

        # Fresh Faker(seed=42) for the second pass — content hashing should
        # not depend on Faker state.
        from faker import Faker
        from seed.faker_providers import BeekeepingProvider

        f2 = Faker(locale="en_US")
        Faker.seed(42)
        f2.add_provider(BeekeepingProvider)
        second = [m.external_id for m in self._gen(f2)]
        assert first == second, (
            "external_ids drift across reseeds — gmail-fixtures will churn"
        )


class TestDocumentPromotion:
    """Each persisted email also goes into the `documents` table so the
    body is searchable via RAG. These tests pin the contract."""

    def _setup_emails(self, db, faker_instance, inbox_fixture_root):
        regions = regions_gen.generate()
        customers = customers_gen.generate(faker_instance)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        import seed.emails as emails_mod
        emails_mod.GMAIL_FIXTURE_ROOT = inbox_fixture_root
        msgs = emails_gen.generate(faker_instance, customers, apiarists)
        emails_gen.persist(db, msgs)
        return msgs

    def test_each_email_creates_a_document(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        msgs = self._setup_emails(db, faker_instance, inbox_fixture_root)
        n_docs = db.execute(
            text(
                "SELECT count(*) FROM documents WHERE source_type = 'email'"
            )
        ).scalar()
        assert n_docs == len(msgs)

    def test_document_metadata_includes_subject_and_from(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        self._setup_emails(db, faker_instance, inbox_fixture_root)
        rows = db.execute(
            text(
                "SELECT metadata FROM documents WHERE source_type = 'email'"
            )
        ).all()
        for (meta,) in rows:
            for key in ("source_type", "external_id", "subject", "from_address",
                        "account_id", "received_at"):
                assert key in meta, f"email doc metadata missing {key!r}: {meta}"
            assert meta["subject"], "empty subject"
            assert meta["from_address"].startswith("contact@"), (
                f"unexpected from_address shape: {meta['from_address']}"
            )

    def test_document_content_includes_subject_line(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        """The chunker/embedder will see this content. Including the subject
        in the body content boosts retrieval recall on subject-line keywords."""
        self._setup_emails(db, faker_instance, inbox_fixture_root)
        sample = db.execute(
            text(
                "SELECT content FROM documents WHERE source_type = 'email' LIMIT 1"
            )
        ).first()
        assert sample is not None
        assert sample[0].startswith("Subject:"), (
            f"email document content missing 'Subject:' prefix: "
            f"{sample[0][:80]!r}"
        )
