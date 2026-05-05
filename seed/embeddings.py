"""Chunk + embed all unprocessed documents into the chunks table.

Multi-source: handles every `documents.source_type` produced by the
seed pipeline — `customer_report`, `email`, `product_data_sheet`,
`regulatory_doc`. The chunker is source-aware (different content
shapes get different split strategies).

Each chunk's metadata inherits the parent document's metadata and
adds `chunk_index` + `chunk_total` so the Sales Copilot can present
"chunk 2 of 3" context, plus a `chunk_chars` field for quick filtering
out tiny chunks.

Run order: this MUST run after every documents-producing seed step
(inspections, emails, product_data_sheets, regulatory_docs). The
`run()` function processes only unchunked documents, so it's safe to
call repeatedly.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger("seed.embeddings")

# Tighter chunking than the prior 1500-char threshold. With the
# LLM-authored corpus most documents are 1200-2000 chars, so a ~700-char
# target produces 2-3 meaningful chunks per doc and gives the retrieval
# layer something to actually rank.
CHUNK_TARGET_CHARS = 700
CHUNK_OVERLAP_CHARS = 120
MIN_CHUNK_CHARS = 200
EMBEDDING_MODEL = os.environ.get(
    "LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _split_sentences(text_in: str) -> list[str]:
    """Cheap sentence split — adequate for narrative + email + reg-doc text."""
    parts = _SENTENCE_BREAK.split(text_in.strip())
    return [p.strip() for p in parts if p.strip()]


def _chunk_text(text_in: str) -> list[str]:
    """Greedy sentence-aware chunker.

    Walks the sentence list, accumulating into a buffer until the next
    sentence would push it past `CHUNK_TARGET_CHARS`. On flush, retains
    the trailing `CHUNK_OVERLAP_CHARS` worth of text in the next chunk
    for context continuity.

    Falls back to single-chunk for short inputs (< MIN_CHUNK_CHARS * 2).
    """
    text_in = text_in.strip()
    if not text_in:
        return []
    if len(text_in) < MIN_CHUNK_CHARS * 2:
        return [text_in]

    sentences = _split_sentences(text_in)
    if not sentences:
        return [text_in]

    chunks: list[str] = []
    buf = ""
    for s in sentences:
        candidate = (buf + " " + s).strip() if buf else s
        if len(candidate) > CHUNK_TARGET_CHARS and buf:
            chunks.append(buf.strip())
            # Carry the tail of buf as overlap context.
            tail = buf[-CHUNK_OVERLAP_CHARS:] if len(buf) > CHUNK_OVERLAP_CHARS else ""
            buf = (tail + " " + s).strip() if tail else s
        else:
            buf = candidate
    if buf and len(buf) >= MIN_CHUNK_CHARS:
        chunks.append(buf.strip())
    elif buf and chunks:
        # Tiny tail — append to the previous chunk rather than orphaning.
        chunks[-1] = (chunks[-1] + " " + buf).strip()
    elif buf:
        chunks.append(buf.strip())
    return chunks


def _load_model():
    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

    return SentenceTransformer(EMBEDDING_MODEL)


def _build_chunk_metadata(
    parent: dict, chunk_text: str, chunk_index: int, chunk_total: int
) -> dict:
    """Return metadata to attach to one chunk row.

    Inherits the parent document's metadata wholesale (which includes
    source_type + type-specific fields), adds positional info, and
    records the chunk's own length for quick filtering.
    """
    parent_meta = dict(parent or {})
    parent_meta.update(
        {
            "chunk_index": chunk_index,
            "chunk_total": chunk_total,
            "chunk_chars": len(chunk_text),
        }
    )
    return parent_meta


def run(db: Session | None = None) -> None:
    """Chunk + embed every unprocessed document. Idempotent.

    Side effects:
      * Loads the sentence-transformers model (once per process).
      * INSERTs into `chunks`.
    """
    own_session = db is None
    if db is None:
        db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql://agentic_mcp_crm:agentic_mcp_crm@postgres:5432/agentic_mcp_crm",
        )
        engine = create_engine(db_url, future=True)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()

    try:
        rows = db.execute(
            text(
                "SELECT d.id AS doc_id, d.source_type, d.content, d.metadata "
                "FROM documents d "
                "LEFT JOIN chunks c ON c.document_id = d.id "
                "WHERE c.id IS NULL"
            )
        ).mappings().all()

        if not rows:
            log.info("no unchunked documents; skipping embed step")
            return

        model = _load_model()
        total_chunks = 0
        per_type: dict[str, int] = {}

        for doc in rows:
            chunks = _chunk_text(doc["content"] or "")
            if not chunks:
                continue
            vecs = model.encode(chunks, normalize_embeddings=True)
            for i, (chunk_text_, vec) in enumerate(zip(chunks, vecs)):
                chunk_meta = _build_chunk_metadata(
                    doc["metadata"], chunk_text_, i, len(chunks)
                )
                db.execute(
                    text(
                        "INSERT INTO chunks "
                        "(id, document_id, content, embedding, metadata) "
                        "VALUES (:id, :doc_id, :content, "
                        "        CAST(:emb AS vector), CAST(:meta AS jsonb))"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "doc_id": doc["doc_id"],
                        "content": chunk_text_,
                        "emb": list(vec.tolist()),
                        "meta": json.dumps(chunk_meta),
                    },
                )
                total_chunks += 1
                per_type[doc["source_type"]] = per_type.get(doc["source_type"], 0) + 1

        if own_session:
            db.commit()
        log.info(
            "embedded %d documents → %d chunks (by source_type: %s)",
            len(rows),
            total_chunks,
            per_type,
        )

    finally:
        if own_session:
            db.close()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    run()
    sys.exit(0)
