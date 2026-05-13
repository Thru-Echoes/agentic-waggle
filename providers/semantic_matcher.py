"""Semantic matcher — resolves free-text customer / SKU references.

Public surface:

    match_customer(hint, conversation_ctx, db) -> CustomerMatch
    match_sku(query, sku_context, db) -> SkuMatch
    reindex_customer(customer_id, db) -> None
    reindex_sku(product_id=..., service_id=..., db) -> None
    backfill_all(db) -> int

Pipeline (both customer + SKU paths):

    Stage A — Hybrid retrieve:
        embed(query) -> pgvector cosine over <table>.embedding (top 20)
        +
        plainto_tsquery(query) -> ts_rank over <table>.tsv (top 20)
        -> Reciprocal Rank Fusion (k=60) -> top 5 fused candidates
        -> multi-source boost (when 2+ entries from the same target
          rank in top-N, sum their fused scores)

    Stage B — LLM disambiguate (added in Task 4):
        gpt-5.4-nano with structured output (response_format=json_schema)
        bound to CustomerMatch / SkuMatch.

    Stage C — Cache + observability (added in Task 5).

Side effects:
    * DB reads (Stage A; one SQL statement with CTE).
    * OpenAI API call (Stage B).
    * In-memory cache write (Stage C).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from providers.vector_search import VectorSearchProvider

logger = logging.getLogger(__name__)


RRF_K = 60
TOP_K = 5
RETRIEVE_LIMIT = 20  # per-source before fusion
PROMPT_VERSION = "v1"

# ivfflat index probe count — controls recall vs. speed trade-off.
# Default of 1 is too low for small datasets (tests with ~140 rows).
# 10 gives ~31% cluster coverage for lists=32; raise at the call site or
# via environment override when the table grows past ~50k rows.
IVFFLAT_PROBES = 10


# ---------------------------------------------------------------------------
# Internal data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One shortlisted entry from Stage A.

    Hides the entry-row vs. target-row distinction; downstream consumers
    care about the target (customer_id / product_id / service_id).
    """

    entry_id: uuid.UUID
    customer_id: uuid.UUID | None
    product_id: uuid.UUID | None
    service_id: uuid.UUID | None
    entry_text: str
    source: str
    dense_rank: int | None
    fts_rank: int | None
    dense_score: float | None
    fts_score: float | None
    fused_score: float


# ---------------------------------------------------------------------------
# Stage A — Hybrid retrieve
# ---------------------------------------------------------------------------


_vector_provider: VectorSearchProvider | None = None


def _get_vector_provider() -> VectorSearchProvider:
    """Return the shared sentence-transformers provider.

    Lazy-init so model-free unit tests don't pay the startup cost.
    Side effect: downloads model weights on first call (~80 MB).
    """
    global _vector_provider
    if _vector_provider is None:
        _vector_provider = VectorSearchProvider()
    return _vector_provider


def _normalize_for_index_and_query(s: str) -> str:
    """Lightweight normalization applied to both index entries and queries.

    Lowercases, collapses whitespace, strips ASCII punctuation that
    interferes with FTS tokenization while keeping internal hyphens
    (e.g., 'back-40' stays as one token).

    Parameters:
        s: Raw input string.

    Returns:
        Normalized string safe for FTS and embedding.

    Side effects: none.
    """
    s = (s or "").lower().strip()
    for ch in [",", ".", "'", '"', "(", ")", "/", "&", "!", "?", ":", ";"]:
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _hybrid_retrieve(
    query: str,
    *,
    table: Literal["customer_search_entries", "sku_search_entries"],
    db: Session,
) -> list[Candidate]:
    """Hybrid retrieve: dense (pgvector cosine) + lexical (Postgres FTS) fused
    via Reciprocal Rank Fusion with a multi-source boost.

    Parameters:
        query: Free-text input from a user or conversation context.
        table: Which search-entry table to query.
        db: SQLAlchemy Session for DB reads.

    Returns:
        Up to TOP_K Candidate objects sorted by fused_score descending.
        Returns [] for an empty query or when no rows match.

    Side effects: DB reads (SET LOCAL + one CTE query within the current
        transaction).
    """
    normalized = _normalize_for_index_and_query(query)
    if not normalized:
        return []

    provider = _get_vector_provider()
    embedding = provider.embed(normalized)

    # Raise probe count so small datasets (tests) get full ivfflat coverage.
    # SET LOCAL is transaction-scoped and does not persist beyond the call.
    db.execute(text(f"SET LOCAL ivfflat.probes = {IVFFLAT_PROBES}"))

    if table == "customer_search_entries":
        target_cols = "customer_id, NULL::uuid AS product_id, NULL::uuid AS service_id"
        group_col = "customer_id"
    else:
        target_cols = "NULL::uuid AS customer_id, product_id, service_id"
        group_col = "COALESCE(product_id::text, service_id::text)"

    sql = f"""
    WITH dense AS (
      SELECT
        id, {target_cols}, entry_text, source,
        1 - (embedding <=> CAST(:emb AS vector)) AS dense_score,
        ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:emb AS vector)) AS dense_rank
      FROM {table}
      ORDER BY embedding <=> CAST(:emb AS vector)
      LIMIT :retrieve_limit
    ),
    fts AS (
      SELECT
        id, {target_cols}, entry_text, source,
        ts_rank(tsv, plainto_tsquery('english', :q)) AS fts_score,
        ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, plainto_tsquery('english', :q)) DESC) AS fts_rank
      FROM {table}
      WHERE tsv @@ plainto_tsquery('english', :q)
      ORDER BY fts_score DESC
      LIMIT :retrieve_limit
    ),
    fused AS (
      SELECT
        COALESCE(dense.id, fts.id) AS id,
        COALESCE(dense.customer_id, fts.customer_id) AS customer_id,
        COALESCE(dense.product_id, fts.product_id) AS product_id,
        COALESCE(dense.service_id, fts.service_id) AS service_id,
        COALESCE(dense.entry_text, fts.entry_text) AS entry_text,
        COALESCE(dense.source, fts.source) AS source,
        dense.dense_rank, dense.dense_score,
        fts.fts_rank, fts.fts_score,
        COALESCE(1.0 / ({RRF_K} + dense.dense_rank), 0) +
        COALESCE(1.0 / ({RRF_K} + fts.fts_rank), 0) AS rrf_score
      FROM dense
      FULL OUTER JOIN fts USING (id)
    )
    SELECT
      id, customer_id, product_id, service_id, entry_text, source,
      dense_rank, fts_rank, dense_score, fts_score,
      rrf_score + COALESCE(
        SUM(rrf_score) OVER (PARTITION BY {group_col})
          - rrf_score, 0
      ) AS boosted_score
    FROM fused
    ORDER BY boosted_score DESC
    LIMIT :top_k
    """

    rows = db.execute(
        text(sql),
        {
            "emb": embedding,
            "q": normalized,
            "retrieve_limit": RETRIEVE_LIMIT,
            "top_k": TOP_K,
        },
    ).mappings().all()

    candidates: list[Candidate] = []
    for r in rows:
        candidates.append(
            Candidate(
                entry_id=uuid.UUID(str(r["id"])),
                customer_id=uuid.UUID(str(r["customer_id"])) if r.get("customer_id") else None,
                product_id=uuid.UUID(str(r["product_id"])) if r.get("product_id") else None,
                service_id=uuid.UUID(str(r["service_id"])) if r.get("service_id") else None,
                entry_text=r["entry_text"],
                source=r["source"],
                dense_rank=r.get("dense_rank"),
                fts_rank=r.get("fts_rank"),
                dense_score=float(r["dense_score"]) if r.get("dense_score") is not None else None,
                fts_score=float(r["fts_score"]) if r.get("fts_score") is not None else None,
                fused_score=float(r["boosted_score"]),
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Backfill (used by tests + seed + boot.py reindex-if-empty)
# Stub here so tests can call it; full impl lives in Task 7.
# ---------------------------------------------------------------------------


def backfill_all(db: Session) -> int:
    """Populate both search-entry tables from customers/sites/products/services.

    Idempotent: TRUNCATEs the tables, then re-inserts. Returns total row count.
    Real implementation in Task 7 (this stub is a placeholder).

    Parameters:
        db: SQLAlchemy Session for DB reads and writes.

    Returns:
        Total number of rows inserted.

    Side effects: TRUNCATEs and re-inserts into customer_search_entries and
        sku_search_entries.
    """
    raise NotImplementedError(
        "backfill_all is implemented in Task 7 of the matcher plan; "
        "called from Stage-A tests via the seeded_customer_entries fixture."
    )
