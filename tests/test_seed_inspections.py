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


class TestTagsAndRichMetadata:
    """Coverage for the LLM-authored fixture's per-report tags + rich
    document metadata that powers hybrid retrieval."""

    def _setup_with_inspections(self, db, faker_instance):
        regions = regions_gen.generate()
        regions_gen.persist(db, regions)
        customers = customers_gen.generate(faker_instance)
        customers_gen.persist(db, customers)
        sites = sites_gen.generate(faker_instance, customers, regions)
        sites_gen.persist(db, sites)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        apiarists_gen.persist(db, apiarists)
        reports = inspections_gen.generate(faker_instance, sites, apiarists)
        inspections_gen.persist(db, reports)
        return reports

    def test_every_report_has_at_least_one_tag(self, db, faker_instance) -> None:
        self._setup_with_inspections(db, faker_instance)
        rows = db.execute(
            text(
                "SELECT id, jsonb_array_length(tags) AS n FROM customer_reports"
            )
        ).all()
        zero_tag_ids = [r.id for r in rows if r.n == 0]
        assert not zero_tag_ids, (
            f"reports missing tags (LLM author dropped them): {zero_tag_ids}"
        )

    def test_tags_are_strings_in_snake_case(self, db, faker_instance) -> None:
        """Tag conventions: short snake_case strings, no whitespace, ≤40 chars."""
        self._setup_with_inspections(db, faker_instance)
        all_tags: list[str] = []
        for (tags,) in db.execute(
            text("SELECT tags FROM customer_reports")
        ).all():
            all_tags.extend(tags)
        bad = [
            t
            for t in all_tags
            if not isinstance(t, str)
            or not t
            or " " in t
            or len(t) > 40
        ]
        assert not bad, f"tags violating convention: {bad[:10]}"

    def test_tropilaelaps_present_in_corpus(self, db, faker_instance) -> None:
        """Federal-quarantine scenario: at least one report must reference
        Tropilaelaps so the Sales Copilot can demonstrate the high-stakes
        retrieval path. Removed from fixture? Update either this test or
        the fixture spec — never both silently."""
        self._setup_with_inspections(db, faker_instance)
        n = db.execute(
            text(
                "SELECT count(*) FROM customer_reports "
                "WHERE diseases_observed @> '[\"TROPILAELAPS\"]'::jsonb"
            )
        ).scalar()
        assert n is not None and n >= 1, (
            "no Tropilaelaps reports in corpus — federal-quarantine "
            "scenario lost. Fix the fixture spec or this test."
        )

    def test_documents_have_rich_metadata(self, db, faker_instance) -> None:
        """Each customer-report document carries the structured fields
        downstream chunkers + filters need."""
        self._setup_with_inspections(db, faker_instance)
        rows = db.execute(
            text(
                "SELECT metadata FROM documents "
                "WHERE source_type = 'customer_report'"
            )
        ).all()
        assert rows
        required = {
            "source_type",
            "customer_site_id",
            "customer_name",
            "site_name",
            "visit_date",
            "author_email",
            "hives_placed",
            "varroa_count",
            "diseases_observed",
            "tags",
        }
        for (meta,) in rows:
            missing = required - meta.keys()
            assert not missing, (
                f"customer_report doc metadata missing keys {missing}: {meta}"
            )

    def test_customer_site_id_resolves_to_real_site(
        self, db, faker_instance
    ) -> None:
        """The metadata's customer_site_id must be a real UUID, joinable
        back to customer_sites — otherwise hybrid filtering by site fails
        silently for that doc."""
        self._setup_with_inspections(db, faker_instance)
        orphans = db.execute(
            text(
                "SELECT count(*) FROM documents d "
                "LEFT JOIN customer_sites cs "
                "  ON cs.id::text = d.metadata->>'customer_site_id' "
                "WHERE d.source_type = 'customer_report' AND cs.id IS NULL"
            )
        ).scalar()
        assert orphans == 0, (
            f"{orphans} customer_report docs have unresolvable customer_site_id"
        )
