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

import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from providers.llm import LLMConfigurationError, get_openai_client
from providers.semantic_matcher_prompts import (
    CUSTOMER_DISAMBIG_SYSTEM,
    CUSTOMER_DISAMBIG_USER_TEMPLATE,
    SKU_DISAMBIG_SYSTEM,
    SKU_DISAMBIG_USER_TEMPLATE,
)
from providers.vector_search import VectorSearchProvider

if TYPE_CHECKING:
    from agents.schemas import (
        ConversationContext,
        CustomerMatch,
        SkuContext,
        SkuMatch,
    )

logger = logging.getLogger(__name__)


RRF_K = 60
TOP_K = 5
RETRIEVE_LIMIT = 20  # per-source before fusion
PROMPT_VERSION = "v1"
LLM_DISAMBIG_MAX_ATTEMPTS = 2
# Multiplier applied to the top candidate's fused_score when the LLM picks an
# id outside our shortlist and we fall back to the top Stage-A result.
# Half-confidence signals that we are uncertain about the fallback.
DEFENSE_CHECK_CONFIDENCE_PENALTY = 0.5

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
# Stage B — LLM disambiguate
# ---------------------------------------------------------------------------


def _format_customer_candidate_block(
    candidates: list[Candidate], db: Session
) -> str:
    """Render candidates as a numbered list for the LLM prompt.

    Looks up each customer's canonical name via the DB for the prompt
    (entry_text might be an alias or site — we want canonical names).

    Parameters:
        candidates: Shortlisted Candidate objects from Stage A.
        db: SQLAlchemy Session for DB reads.

    Returns:
        Numbered multi-line string describing each candidate.

    Side effects: DB read (SELECT from customers).
    """
    if not candidates:
        return "(empty)"
    customer_ids = list({c.customer_id for c in candidates if c.customer_id})
    name_rows = (
        db.execute(
            text("SELECT id, name FROM customers WHERE id = ANY(:ids)"),
            {"ids": customer_ids},
        )
        .mappings()
        .all()
    )
    id_to_name = {r["id"]: r["name"] for r in name_rows}

    lines: list[str] = []
    for i, c in enumerate(candidates, 1):
        if not c.customer_id:
            continue
        name = id_to_name.get(c.customer_id, "(unknown)")
        lines.append(
            f"{i}. customer_id={c.customer_id} name={name!r} "
            f"matched on source={c.source} entry={c.entry_text!r} "
            f"(fused_score={c.fused_score:.4f})"
        )
    return "\n".join(lines)


def _format_sku_candidate_block(
    candidates: list[Candidate], db: Session
) -> str:
    """Render SKU candidates for the LLM prompt.

    Parameters:
        candidates: Shortlisted Candidate objects from Stage A.
        db: SQLAlchemy Session for DB reads.

    Returns:
        Numbered multi-line string describing each SKU candidate.

    Side effects: DB reads (SELECT from products and/or services).
    """
    if not candidates:
        return "(empty)"
    lines: list[str] = []
    for i, c in enumerate(candidates, 1):
        if c.product_id:
            row = db.execute(
                text("SELECT sku, name FROM products WHERE id = :pid"),
                {"pid": c.product_id},
            ).mappings().first()
            if row:
                lines.append(
                    f"{i}. kind=product product_id={c.product_id} "
                    f"sku={row['sku']!r} name={row['name']!r} "
                    f"matched on source={c.source} entry={c.entry_text!r} "
                    f"(fused_score={c.fused_score:.4f})"
                )
        elif c.service_id:
            row = db.execute(
                text("SELECT sku, name FROM services WHERE id = :sid"),
                {"sid": c.service_id},
            ).mappings().first()
            if row:
                lines.append(
                    f"{i}. kind=service service_id={c.service_id} "
                    f"sku={row['sku']!r} name={row['name']!r} "
                    f"matched on source={c.source} entry={c.entry_text!r} "
                    f"(fused_score={c.fused_score:.4f})"
                )
    return "\n".join(lines)


def _llm_disambiguate(
    *,
    query: str,
    candidates: list[Candidate],
    kind: Literal["customer", "sku"],
    ctx: "ConversationContext | SkuContext",
    db: Session,
) -> "CustomerMatch | SkuMatch":
    """Stage B — call gpt-5.4-nano with structured output bound to the
    matcher's Pydantic schema. Raises on auth failure; retries once on
    network glitch; returns a low-confidence null on terminal failure.

    Parameters:
        query: The raw user text to disambiguate.
        candidates: Stage A shortlist (up to TOP_K=5 Candidate objects).
        kind: "customer" or "sku" — controls which schema + prompt is used.
        ctx: ConversationContext (customer path) or SkuContext (sku path).
        db: SQLAlchemy Session for DB reads (name lookups for prompt rendering).

    Returns:
        CustomerMatch or SkuMatch (matching kind). customer_id / sku_kind
        will be None if the LLM cannot pick confidently.

    Side effects:
        * One OpenAI API call (two on transient network failure).
        * Logs at INFO on each call; WARNING on retry; ERROR on terminal failure.

    Raises:
        LLMConfigurationError: when OPENAI_API_KEY is not set. Propagated
            intentionally — misconfigured env is not a transient failure.
    """
    # Deferred imports to avoid circular import at module load time.
    from agents.schemas import (
        ConversationContext,
        CustomerMatch,
        SkuMatch,
    )
    from app.config import get_settings

    settings = get_settings()
    client = get_openai_client()
    model = settings.copilot_matcher_disambig_model  # default: gpt-5.4-nano

    if kind == "customer":
        assert isinstance(ctx, ConversationContext)
        active_name: str | None = None
        recent_names: list[str] = []
        if ctx.active_account_id:
            row = db.execute(
                text("SELECT name FROM customers WHERE id = :id"),
                {"id": ctx.active_account_id},
            ).first()
            if row:
                active_name = row.name
        if ctx.last_3_customer_ids:
            rows = db.execute(
                text("SELECT name FROM customers WHERE id = ANY(:ids)"),
                {"ids": list(ctx.last_3_customer_ids)},
            ).all()
            recent_names = [r.name for r in rows]

        candidate_block = _format_customer_candidate_block(candidates, db)
        user_msg = CUSTOMER_DISAMBIG_USER_TEMPLATE.format(
            query=query,
            active_account_name=active_name,
            recent_customer_names=recent_names,
            candidate_block=candidate_block,
        )
        system_msg = CUSTOMER_DISAMBIG_SYSTEM
        schema: dict[str, Any] = CustomerMatch.model_json_schema()
        match_class: type[CustomerMatch] | type[SkuMatch] = CustomerMatch
    else:
        candidate_block = _format_sku_candidate_block(candidates, db)
        user_msg = SKU_DISAMBIG_USER_TEMPLATE.format(
            query=query,
            candidate_block=candidate_block,
        )
        system_msg = SKU_DISAMBIG_SYSTEM
        schema = SkuMatch.model_json_schema()
        match_class = SkuMatch

    # Strip pydantic-internal keys not understood by OpenAI's json_schema mode
    schema.pop("$defs", None)

    logger.info(
        "LLM disambig: kind=%s query=%r model=%s candidates=%d",
        kind,
        query,
        model,
        len(candidates),
    )

    last_error: Exception | None = None
    for attempt in range(1, LLM_DISAMBIG_MAX_ATTEMPTS + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": match_class.__name__,
                        "schema": schema,
                        "strict": False,
                    },
                },
                temperature=0,
            )
            raw = resp.choices[0].message.content or "{}"
            parsed: dict[str, Any] = json.loads(raw)

            # Defensive: strip malformed alternatives rather than letting
            # them abort the whole match. The LLM often omits required fields
            # from alternatives; drop any entry that would fail validation so
            # the primary match still comes through.
            if kind == "customer" and isinstance(parsed.get("alternatives"), list):
                before = len(parsed["alternatives"])
                parsed["alternatives"] = [
                    alt for alt in parsed["alternatives"]
                    if isinstance(alt, dict) and "source" in alt
                ]
                dropped = before - len(parsed["alternatives"])
                if dropped:
                    logger.warning(
                        "LLM omitted 'source' from %d customer alternative(s); dropped before validate",
                        dropped,
                    )
            elif kind == "sku" and isinstance(parsed.get("alternatives"), list):
                before = len(parsed["alternatives"])
                parsed["alternatives"] = [
                    alt for alt in parsed["alternatives"]
                    if isinstance(alt, dict)
                    and "sku_kind" in alt
                    and "sku_code" in alt
                    and "name" in alt
                    and "confidence" in alt
                ]
                dropped = before - len(parsed["alternatives"])
                if dropped:
                    logger.warning(
                        "LLM omitted required fields from %d SKU alternative(s); dropped before validate",
                        dropped,
                    )

            match = match_class.model_validate(parsed)

            # Defense: if the LLM picked an id not in our shortlist, discard.
            if kind == "customer":
                assert isinstance(match, CustomerMatch)
                valid_ids = {c.customer_id for c in candidates}
                if match.customer_id and match.customer_id not in valid_ids:
                    logger.warning(
                        "LLM picked customer_id=%s not in shortlist; falling back",
                        match.customer_id,
                    )
                    top = candidates[0] if candidates else None
                    return CustomerMatch(
                        customer_id=top.customer_id if top else None,
                        customer_name=None,
                        confidence=(top.fused_score * DEFENSE_CHECK_CONFIDENCE_PENALTY) if top else 0.0,
                        evidence=[query],
                        alternatives=[],
                    )
            return match
        except LLMConfigurationError:
            # Auth failure — propagate immediately; not a transient error.
            raise
        except Exception as e:
            last_error = e
            if attempt == 1:
                logger.warning(
                    "LLM disambig attempt 1 failed: %s; retrying", e
                )
                continue
            logger.error("LLM disambig terminal failure: %s", e)
            break

    # Terminal failure — return a low-confidence null
    logger.error(
        "LLM disambig returning null match after terminal failure: %s", last_error
    )
    if kind == "customer":
        return CustomerMatch(
            customer_id=None,
            customer_name=None,
            confidence=0.0,
            evidence=[],
            alternatives=[],
        )
    else:
        return SkuMatch(
            sku_kind=None,
            product_id=None,
            service_id=None,
            sku_code=None,
            name=None,
            confidence=0.0,
            evidence=[],
            alternatives=[],
        )


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
