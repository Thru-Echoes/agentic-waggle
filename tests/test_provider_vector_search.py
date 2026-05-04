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
