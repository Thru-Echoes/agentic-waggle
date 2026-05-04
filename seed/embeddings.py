"""Chunk + embed all seeded inspection narratives into the chunks table.

Run order matters: this must run AFTER inspections.persist inserts
documents. `seed.generate.run()` calls this last.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import logging
import os
import sys
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger("seed.embeddings")

MAX_CHUNK_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100
EMBEDDING_MODEL = os.environ.get(
    "LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)


def _rough_chunk(text_in: str) -> list[str]:
    """Token-unaware chunker: splits by sentence-ish boundaries.

    For short narratives (<1000 chars) returns a single chunk. For longer
    docs, splits at paragraph boundaries with light overlap.
    """
    text_in = text_in.strip()
    if len(text_in) < 1500:
        return [text_in]
    # Paragraph split; merge short paragraphs together up to a rough limit.
    paragraphs = [p.strip() for p in text_in.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 > 3200:  # ~800 tokens * 4 chars
            chunks.append(buf.strip())
            buf = p
        else:
            buf = (buf + "\n\n" + p) if buf else p
    if buf:
        chunks.append(buf.strip())
    return chunks


def _load_model():
    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

    return SentenceTransformer(EMBEDDING_MODEL)


def run(db: Session | None = None) -> None:
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
        docs = db.execute(
            text(
                "SELECT d.id AS doc_id, d.content, d.metadata "
                "FROM documents d "
                "LEFT JOIN chunks c ON c.document_id = d.id "
                "WHERE c.id IS NULL"
            )
        ).mappings().all()

        if not docs:
            log.info("no unchunked documents; skipping embed step")
            return

        model = _load_model()

        for doc in docs:
            chunks = _rough_chunk(doc["content"] or "")
            if not chunks:
                continue
            vecs = model.encode(chunks, normalize_embeddings=True)
            for chunk_text_, vec in zip(chunks, vecs):
                db.execute(
                    text(
                        "INSERT INTO chunks (id, document_id, content, embedding, metadata) "
                        "VALUES (:id, :doc_id, :content, CAST(:emb AS vector), CAST(:meta AS jsonb))"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "doc_id": doc["doc_id"],
                        "content": chunk_text_,
                        "emb": list(vec.tolist()),
                        "meta": json.dumps(doc["metadata"] or {}),
                    },
                )
        if own_session:
            db.commit()
        log.info("embedded %d documents", len(docs))

    finally:
        if own_session:
            db.close()


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    run()
    sys.exit(0)
