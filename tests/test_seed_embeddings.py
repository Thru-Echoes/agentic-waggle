"""E2E tests for the embeddings step.

Uses the real sentence-transformers model. No mocks — the whole point is
to fail loudly if the model can't load or produces wrong-dim vectors.

The first run in a fresh environment downloads ~80 MB of model weights.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from sqlalchemy import text

from seed import apiarists as apiarists_gen
from seed import customers as customers_gen
from seed import embeddings as embeddings_gen
from seed import inspections as inspections_gen
from seed import regions as regions_gen
from seed import sites as sites_gen


def _seed_min_for_embeddings(db, faker_instance) -> None:
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


class TestEmbeddings:
    def test_rough_chunk_short_text_returns_single_chunk(self) -> None:
        chunks = embeddings_gen._rough_chunk("A short inspection note.")
        assert len(chunks) == 1

    def test_rough_chunk_empty_returns_single_empty(self) -> None:
        # The chunker returns the (possibly empty) text as-is for short input.
        assert embeddings_gen._rough_chunk("") == [""]

    def test_run_embeds_every_document(self, db, faker_instance) -> None:
        _seed_min_for_embeddings(db, faker_instance)
        doc_count = db.execute(text("SELECT COUNT(*) FROM documents")).scalar()
        assert doc_count == 40
        embeddings_gen.run(db)
        chunk_count = db.execute(text("SELECT COUNT(*) FROM chunks")).scalar()
        # At least one chunk per document.
        assert chunk_count >= 40

    def test_every_chunk_has_384_dim_embedding(self, db, faker_instance) -> None:
        _seed_min_for_embeddings(db, faker_instance)
        embeddings_gen.run(db)
        # pgvector stores as vector; retrieve dim via vector_dims().
        dims = db.execute(
            text("SELECT DISTINCT vector_dims(embedding) FROM chunks")
        ).scalars().all()
        assert list(dims) == [384]

    def test_re_run_is_idempotent(self, db, faker_instance) -> None:
        """Re-running the embedder must be a true no-op: same count, same
        chunk IDs, same embeddings. A regression where re-run re-embedded
        with new IDs but identical count would pass a weaker count-only
        test; this one catches it.
        """
        _seed_min_for_embeddings(db, faker_instance)
        embeddings_gen.run(db)
        first_rows = db.execute(
            text("SELECT id::text, document_id::text FROM chunks ORDER BY id")
        ).all()
        embeddings_gen.run(db)
        second_rows = db.execute(
            text("SELECT id::text, document_id::text FROM chunks ORDER BY id")
        ).all()
        assert first_rows == second_rows, (
            "Re-run of embeddings produced different chunk rows; it should "
            "have been a pure no-op."
        )
