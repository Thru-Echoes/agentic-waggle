"""Vector search provider — sentence-transformers + pgvector cosine search.

Embeddings are generated locally via the `sentence-transformers/all-MiniLM-L6-v2`
model (384-dim). Search is a raw pgvector cosine-similarity query against the
`chunks` table. No network calls; first use downloads ~80 MB of model weights
into the container's HuggingFace cache.

Public surface:

    class VectorSearchProvider:
        def embed(text: str) -> list[float]
        def embed_batch(texts: list[str]) -> list[list[float]]
        def search(query: str, top_k: int, filters: dict | None) -> list[SearchResult]

Side effects: `search` executes SQL against the provided DB session.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

EMBEDDING_MODEL = os.environ.get(
    "LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
EMBEDDING_DIM = 384


@lru_cache(maxsize=1)
def _model() -> Any:
    """Return the singleton sentence-transformers model.

    Imported lazily so model-free unit tests don't pay the startup cost.
    Side effect: downloads model weights on first call.
    """
    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

    return SentenceTransformer(EMBEDDING_MODEL)


@dataclass
class SearchResult:
    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any]


class VectorSearchProvider:
    """Local pgvector + sentence-transformers vector search.

    Accepts an optional SQLAlchemy session at construction; `search` requires
    a session (either here or passed per call). **kwargs on the constructor
    are accepted and ignored so callers can be configuration-agnostic.
    """

    def __init__(self, db: Session | None = None, **_: Any) -> None:
        self.db = db

    def embed(self, text_input: str) -> list[float]:
        """Return a 384-dim embedding. Raises if the model misbehaves."""
        vec = _model().encode([text_input], normalize_embeddings=True)[0]
        out = vec.tolist()
        if len(out) != EMBEDDING_DIM:
            raise RuntimeError(
                f"Expected {EMBEDDING_DIM}-dim embedding; got {len(out)}. "
                f"LOCAL_EMBEDDING_MODEL is {EMBEDDING_MODEL!r}; check it "
                f"produces {EMBEDDING_DIM}-dim vectors."
            )
        return out

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return a list of 384-dim embeddings. Empty list in, empty list out."""
        if not texts:
            return []
        vecs = _model().encode(texts, normalize_embeddings=True)
        out = [v.tolist() for v in vecs]
        for i, v in enumerate(out):
            if len(v) != EMBEDDING_DIM:
                raise RuntimeError(
                    f"embed_batch: item {i} is {len(v)}-dim; expected {EMBEDDING_DIM}."
                )
        return out

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Cosine-similarity search over `chunks`. Returns top_k hits.

        Side effects: runs a SQL query against `self.db`.
        Raises RuntimeError if no session was provided at construction.
        """
        if self.db is None:
            raise RuntimeError(
                "VectorSearchProvider.search requires a DB session — "
                "pass one to the constructor."
            )

        query_vec = self.embed(query)
        # SQLAlchemy's `:name` param syntax collides with Postgres's `::`
        # type-cast operator — `:site_id::uuid` is parsed by SA as `:site_id`
        # followed by garbage, producing a syntax error. Use `CAST(... AS T)`
        # everywhere for type-casts so param names and casts never touch.
        sql = """
            SELECT id, content, metadata,
                   1 - (embedding <=> CAST(:qv AS vector)) AS score
            FROM chunks
            WHERE (
                CAST(:site_id AS uuid) IS NULL
                OR CAST(metadata->>'site_id' AS uuid) = CAST(:site_id AS uuid)
            )
              AND (
                :after IS NULL
                OR CAST(metadata->>'visit_date' AS date) >= CAST(:after AS date)
              )
            ORDER BY embedding <=> CAST(:qv AS vector)
            LIMIT :k
        """
        params = {
            "qv": query_vec,
            "site_id": (filters or {}).get("site_id"),
            "after": (filters or {}).get("visit_date_after"),
            "k": top_k,
        }
        rows = self.db.execute(text(sql), params).mappings().all()
        return [
            SearchResult(
                chunk_id=str(r["id"]),
                content=r["content"],
                score=float(r["score"]),
                metadata=r["metadata"] or {},
            )
            for r in rows
        ]
