"""E2E tests for the vector-search provider.

Uses the real sentence-transformers model. No mocks.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text

from providers.vector_search import EMBEDDING_DIM, VectorSearchProvider


class TestEmbedding:
    def test_single_text_returns_correct_dim(self) -> None:
        p = VectorSearchProvider()
        vec = p.embed("almond bloom pollination inspection note")
        assert len(vec) == EMBEDDING_DIM
        assert all(isinstance(x, float) for x in vec)

    def test_batch_returns_one_vector_per_input(self) -> None:
        p = VectorSearchProvider()
        vecs = p.embed_batch(["a", "b", "c"])
        assert len(vecs) == 3
        for v in vecs:
            assert len(v) == EMBEDDING_DIM

    def test_empty_batch_returns_empty(self) -> None:
        p = VectorSearchProvider()
        assert p.embed_batch([]) == []

    def test_semantically_similar_texts_are_closer(self) -> None:
        p = VectorSearchProvider()
        anchor = p.embed("bee disease outbreak varroa mites")
        similar = p.embed("varroa mite infestation in honey bee colonies")
        dissimilar = p.embed("recipe for chocolate chip cookies")
        from math import fsum
        def cos(a, b): return fsum(x * y for x, y in zip(a, b))
        # Normalized vectors → cosine = dot product
        assert cos(anchor, similar) > cos(anchor, dissimilar)


class TestSearch:
    def test_search_without_db_raises(self) -> None:
        p = VectorSearchProvider(db=None)
        with pytest.raises(RuntimeError):
            p.search("test query")

    def test_search_returns_results_from_pgvector(self, db, faker_instance) -> None:
        from seed import apiarists as apiarists_gen
        from seed import customers as customers_gen
        from seed import embeddings as embeddings_gen
        from seed import inspections as inspections_gen
        from seed import regions as regions_gen
        from seed import sites as sites_gen

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
        embeddings_gen.run(db)

        total_chunks = db.execute(text("SELECT COUNT(*) FROM chunks")).scalar()
        assert total_chunks > 0, "embeddings did not populate chunks"

        p = VectorSearchProvider(db=db)
        results = p.search("varroa mite count inspection", top_k=5)
        assert len(results) == 5
        for r in results:
            assert isinstance(r.content, str)
            assert r.content
            assert 0.0 <= r.score <= 1.0


class TestHybridFilters:
    """Hybrid retrieval = semantic ranking with structured-filter pre-selection.
    Each test exercises one filter dimension so a regression localizes."""

    def _full_corpus(self, db, faker_instance, inbox_fixture_root) -> VectorSearchProvider:
        """Seed every source type so cross-source filters have something to
        find/exclude."""
        from seed import generate as seed_generate
        from seed import emails as emails_mod
        import os

        emails_mod.GMAIL_FIXTURE_ROOT = inbox_fixture_root
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
        seed_generate.run(drop_existing=False)
        return VectorSearchProvider(db=db)

    def test_source_type_filter_restricts_to_one_type(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        p = self._full_corpus(db, faker_instance, inbox_fixture_root)
        results = p.search(
            "federal compliance treatment requirements",
            top_k=5,
            filters={"source_type": "regulatory_doc"},
        )
        assert results, "expected at least one regulatory_doc hit"
        for r in results:
            assert r.metadata.get("source_type") == "regulatory_doc", (
                f"source_type filter leaked: got {r.metadata.get('source_type')!r}"
            )

    def test_min_varroa_filter_excludes_low_varroa_chunks(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        p = self._full_corpus(db, faker_instance, inbox_fixture_root)
        results = p.search(
            "treatment threshold mites",
            top_k=10,
            filters={"min_varroa": 3.0},
        )
        # All hits must be from customer_reports with varroa >= 3.0
        assert results, (
            "min_varroa=3.0 produced zero hits — fixture should have ≥1 "
            "high-varroa report"
        )
        for r in results:
            assert r.metadata.get("source_type") == "customer_report", (
                f"min_varroa filter should restrict to customer_reports: {r.metadata}"
            )
            v = r.metadata.get("varroa_count")
            assert v is not None and float(v) >= 3.0, (
                f"min_varroa=3.0 leaked a varroa={v} chunk"
            )

    def test_tag_filter_uses_jsonb_containment(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        p = self._full_corpus(db, faker_instance, inbox_fixture_root)
        # Pick a tag that's actually present in the fixture corpus —
        # query the DB to find one rather than hardcoding (LLM-generated
        # tags vary slightly across regenerations).
        any_tag = db.execute(
            text(
                "SELECT jsonb_array_elements_text(metadata->'tags') "
                "FROM chunks "
                "WHERE metadata->>'source_type' = 'customer_report' LIMIT 1"
            )
        ).scalar()
        assert any_tag is not None, "no tagged chunks present in corpus"

        results = p.search(
            "inspection observation",
            top_k=5,
            filters={"tag": any_tag},
        )
        assert results, f"tag filter for {any_tag!r} produced no hits"
        for r in results:
            assert any_tag in r.metadata.get("tags", []), (
                f"tag filter leaked: hit {r.chunk_id} has tags="
                f"{r.metadata.get('tags')}, expected {any_tag!r}"
            )

    def test_customer_site_id_filter_restricts_by_site(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        p = self._full_corpus(db, faker_instance, inbox_fixture_root)
        # Pick any site that has at least one chunk.
        site_id = db.execute(
            text(
                "SELECT metadata->>'customer_site_id' FROM chunks "
                "WHERE metadata->>'source_type' = 'customer_report' LIMIT 1"
            )
        ).scalar()
        assert site_id

        results = p.search(
            "field inspection",
            top_k=5,
            filters={"customer_site_id": site_id},
        )
        assert results, f"site filter for {site_id} produced no hits"
        for r in results:
            assert r.metadata.get("customer_site_id") == site_id, (
                f"site filter leaked a different site: "
                f"{r.metadata.get('customer_site_id')}"
            )

    def test_legacy_site_id_alias_still_works(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        """Backward compat: callers using `site_id` (old key) get the same
        behavior as `customer_site_id` (new preferred key)."""
        p = self._full_corpus(db, faker_instance, inbox_fixture_root)
        site_id = db.execute(
            text(
                "SELECT metadata->>'customer_site_id' FROM chunks "
                "WHERE metadata->>'source_type' = 'customer_report' LIMIT 1"
            )
        ).scalar()
        a = p.search(
            "inspection",
            top_k=3,
            filters={"site_id": site_id},
        )
        b = p.search(
            "inspection",
            top_k=3,
            filters={"customer_site_id": site_id},
        )
        assert [r.chunk_id for r in a] == [r.chunk_id for r in b]

    def test_combined_filters_AND_semantics(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        """Multiple filters compose via AND — high-varroa AND customer_report."""
        p = self._full_corpus(db, faker_instance, inbox_fixture_root)
        results = p.search(
            "treatment plan needed urgently",
            top_k=5,
            filters={
                "source_type": "customer_report",
                "min_varroa": 3.0,
            },
        )
        assert results
        for r in results:
            assert r.metadata.get("source_type") == "customer_report"
            v = r.metadata.get("varroa_count")
            assert v is not None and float(v) >= 3.0

    def test_unmatched_filter_returns_empty(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        """A filter that no chunk satisfies returns an empty list, not an
        error — tests the SQL filter binds NULL-safely."""
        p = self._full_corpus(db, faker_instance, inbox_fixture_root)
        results = p.search(
            "anything",
            top_k=5,
            filters={"tag": "this_tag_does_not_exist_xyz_12345"},
        )
        assert results == []
