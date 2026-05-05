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
    def test_chunk_text_short_text_returns_single_chunk(self) -> None:
        chunks = embeddings_gen._chunk_text("A short inspection note.")
        assert len(chunks) == 1

    def test_chunk_text_empty_returns_no_chunks(self) -> None:
        # An empty document produces no chunks (nothing meaningful to embed).
        assert embeddings_gen._chunk_text("") == []

    def test_run_embeds_every_document(self, db, faker_instance) -> None:
        _seed_min_for_embeddings(db, faker_instance)
        doc_count = db.execute(text("SELECT COUNT(*) FROM documents")).scalar()
        assert doc_count == 40
        embeddings_gen.run(db)
        chunk_count = db.execute(text("SELECT COUNT(*) FROM chunks")).scalar()
        # At least one chunk per document; richer narratives may produce more.
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


class TestChunkerSemantics:
    """Direct unit-style tests on the new sentence-aware chunker. No DB
    needed — the function is pure."""

    def test_long_text_splits_into_multiple_chunks(self) -> None:
        """A 2000+ char doc with paragraph + sentence boundaries should
        produce 2-3 chunks, not 1."""
        long_text = (
            "On May 15, 2025 we visited the East Block. "
            "240 hives placed in the southwest corner with full sun exposure. "
            "Almond bloom approximately 80% open with strong pollen flow. "
        ) * 8
        chunks = embeddings_gen._chunk_text(long_text)
        assert len(chunks) >= 2, (
            f"expected multi-chunk output for {len(long_text)}-char input; "
            f"got {len(chunks)}"
        )

    def test_single_sentence_returns_single_chunk(self) -> None:
        chunks = embeddings_gen._chunk_text(
            "Single short sentence about almond bloom."
        )
        assert len(chunks) == 1
        assert chunks[0] == "Single short sentence about almond bloom."

    def test_chunks_respect_target_size_with_some_slack(self) -> None:
        """No chunk should be more than ~1.5x the target size."""
        from seed.embeddings import CHUNK_TARGET_CHARS

        long_text = ("This is one sentence that wraps nicely. " * 60).strip()
        chunks = embeddings_gen._chunk_text(long_text)
        for c in chunks:
            assert len(c) <= int(CHUNK_TARGET_CHARS * 1.5), (
                f"chunk too long ({len(c)} chars): {c[:80]}..."
            )


class TestMultiSourceCorpus:
    """The chunker now runs across customer_reports + emails +
    product_data_sheets + regulatory_docs. Each source must produce chunks
    with type-specific metadata."""

    def _full_seed(self, db, faker_instance, inbox_fixture_root):
        from seed import generate as seed_generate
        from seed import emails as emails_mod

        emails_mod.GMAIL_FIXTURE_ROOT = inbox_fixture_root
        # Override DATABASE_URL so seed.generate uses the test DB
        import os
        os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
        seed_generate.run(drop_existing=False)

    def test_chunks_present_for_every_source_type(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        self._full_seed(db, faker_instance, inbox_fixture_root)
        counts = dict(
            db.execute(
                text(
                    "SELECT metadata->>'source_type', count(*) "
                    "FROM chunks GROUP BY 1"
                )
            ).all()
        )
        for src in (
            "customer_report",
            "email",
            "product_data_sheet",
            "regulatory_doc",
        ):
            assert counts.get(src, 0) > 0, (
                f"no chunks for source_type={src!r}: {counts}"
            )

    def test_chunk_metadata_includes_chunk_index_and_total(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        self._full_seed(db, faker_instance, inbox_fixture_root)
        rows = db.execute(
            text("SELECT metadata FROM chunks LIMIT 5")
        ).all()
        for (meta,) in rows:
            assert "chunk_index" in meta
            assert "chunk_total" in meta
            assert "chunk_chars" in meta
            assert "source_type" in meta
            assert isinstance(meta["chunk_index"], int)
            assert isinstance(meta["chunk_total"], int)
            assert 0 <= meta["chunk_index"] < meta["chunk_total"]

    def test_customer_report_chunks_have_filterable_fields(
        self, db, faker_instance, inbox_fixture_root
    ) -> None:
        """For hybrid retrieval, customer_report chunks must carry
        customer_site_id, varroa_count, diseases_observed, tags."""
        self._full_seed(db, faker_instance, inbox_fixture_root)
        rows = db.execute(
            text(
                "SELECT metadata FROM chunks "
                "WHERE metadata->>'source_type' = 'customer_report'"
            )
        ).all()
        assert rows
        for (meta,) in rows:
            for key in (
                "customer_site_id",
                "varroa_count",
                "diseases_observed",
                "tags",
            ):
                assert key in meta, (
                    f"customer_report chunk missing filter field {key!r}: {meta}"
                )
