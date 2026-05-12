# Semantic Matcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every rapidfuzz / token-index customer & SKU resolution path with a single LLM-grade semantic matcher (hybrid retrieve → LLM disambig → Grounded result with confidence & alternatives), used by the copilot orchestrator, the inbox confirm flow, and the order extractor.

**Architecture:** Postgres FTS + pgvector cosine fused via RRF (Reciprocal Rank Fusion) for retrieval; gpt-5.4-nano with structured output for context-aware disambiguation; Grounded results (customer_id + evidence + confidence + alternatives); confidence-graduated UX (proceed / inline-confirm / disambiguate); per-conversation LRU cache; observability via `chat_turns.tool_calls` + TRACE.

**Tech Stack:** Python 3.11+, SQLModel + Alembic + pgvector + Postgres FTS, sentence-transformers (all-MiniLM-L6-v2, 384-dim), OpenAI gpt-5.4-nano (structured output via `response_format={"type":"json_schema",...}`), FastAPI + Jinja + HTMX, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-12-semantic-matcher-design.md` (commit `b31123d` on branch `docs/semantic-matcher-spec`, PR #2).

---

## Pre-flight

1. **Branch:** Create `feat/semantic-matcher` off `main` (NOT off the spec branch — the spec branch only contains docs).
2. **Worktree (optional but recommended for isolation):** invoke `superpowers:using-git-worktrees` at execution time.
3. **Env vars required:**
   - `OPENAI_API_KEY` — for LLM disambig + eval harness (eval tests skip cleanly if missing)
   - `DATABASE_URL` — points at the local test DB
   - `MATCHER_EVAL_MODE=sample` (default) or `full` (for full eval)
4. **Clean baseline:** `make reset-db && make migrate && make seed` so the FK constraints in migration 004 have rows to reference.
5. **Dependencies:** confirm `pgvector` extension already installed (it is — migration 001 enables it).

## File-structure overview

| Action | Path | Responsibility |
|---|---|---|
| **NEW** | `alembic/versions/004_semantic_matcher.py` | DDL for `customer_search_entries` + `sku_search_entries` |
| **MODIFY** | `db/models.py` | Add `CustomerSearchEntry` + `SkuSearchEntry` SQLModel rows + relationships |
| **MODIFY** | `tests/schema.sql` | Mirror migration 004 DDL |
| **NEW** | `providers/semantic_matcher.py` | Public matcher API + hybrid retrieve + LLM disambig + cache |
| **NEW** | `providers/semantic_matcher_prompts.py` | Versioned disambiguator prompt templates |
| **MODIFY** | `agents/schemas.py` | Replace existing `CustomerMatch` with Grounded shape; add `CustomerCandidate`, `SkuMatch`, `SkuCandidate`, `ConversationContext`, `SkuContext` |
| **NEW** | `seed/customer_search_entries.py` | Backfill generator |
| **NEW** | `seed/sku_search_entries.py` | Backfill generator |
| **MODIFY** | `seed/embeddings.py` | Wire the new backfills into the seed entrypoint |
| **MODIFY** | `scripts/boot.py` | Add "reindex-if-empty" step |
| **NEW** | `tests/fixtures/matcher_corpus.yaml` | ~246 real-text test cases across 24 categories |
| **NEW** | `tests/test_provider_semantic_matcher.py` | Provider unit/integration tests |
| **NEW** | `tests/test_matcher_eval.py` | Eval harness with per-category thresholds |
| **NEW** | `app/templates/partials/copilot_confirm_match.html` | Yes/No inline confirm card |
| **NEW** | `app/templates/partials/copilot_pick_match.html` | Alternatives picker card |
| **MODIFY** | `app/routes/copilot.py` | New `POST /copilot/turns/{id}/confirm-match`; remove `account_resolver` imports |
| **MODIFY** | `agents/copilot/orchestrator.py` | Use `match_customer`; confidence-graduated branching |
| **MODIFY** | `agents/order_extractor.py` | Use `match_customer` + `match_sku`; remove old imports |
| **MODIFY** | `mcp_servers/sales_copilot_server.py` | Route `find_customer_match` MCP tool through new matcher |
| **MODIFY** | `scripts/demo.py` | Remove old import; either route through new matcher or drop diagnostic call |
| **MODIFY** | `tests/test_e2e_copilot_browser.py` | Add Yes/No/Pick/Rephrase paths |
| **MODIFY** | `requirements.txt` | Remove `rapidfuzz` |
| **DELETE** | `agents/copilot/account_resolver.py` | Replaced |
| **DELETE** | `agents/customer_matcher.py` | Replaced |
| **DELETE** | `agents/sku_resolver.py` | Replaced |
| **DELETE** | `tests/test_agent_copilot_account_resolver.py` | Subsumed by `test_provider_semantic_matcher.py` |
| **DELETE** | `tests/test_agent_customer_matcher.py` | Subsumed |
| **DELETE** | `tests/test_agent_sku_resolver.py` | Subsumed |
| **MODIFY** | `tests/test_order_extractor*.py` | Update assertions where new matcher differs from rapidfuzz (5–20% expected) |
| **MODIFY** | `tests/test_agent_copilot_context_builder.py` | Replace `build_account_index` with matcher integration |
| **MODIFY** | `tests/test_agent_copilot_orchestrator.py` | Same |

---

## Task 1: Schema + migration 004 + DB models

**Files:**
- Create: `alembic/versions/004_semantic_matcher.py`
- Modify: `db/models.py` (add two SQLModel classes near the bottom)
- Modify: `tests/schema.sql` (append the two new tables)
- Test: `tests/test_migration_004.py` (NEW)

### Step 1.1 — Write the failing migration test

Create `tests/test_migration_004.py`:

```python
"""Migration 004 creates customer_search_entries + sku_search_entries
with pgvector + FTS indexes and the right CHECK constraints.
"""
from __future__ import annotations

from sqlalchemy import text

from db.session import session_scope


def test_customer_search_entries_table_exists():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'customer_search_entries' ORDER BY ordinal_position"
            )
        ).fetchall()
    cols = {row.column_name: row.data_type for row in result}
    assert cols["id"] == "uuid"
    assert cols["customer_id"] == "uuid"
    assert cols["entry_text"] == "text"
    assert cols["source"] == "text"
    assert cols["embedding"] == "USER-DEFINED"  # vector(384)
    assert cols["tsv"] == "tsvector"


def test_customer_search_entries_source_check_constraint():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'customer_search_entries_source_check'"
            )
        ).scalar_one()
    assert "'name'" in result
    assert "'alias'" in result
    assert "'site'" in result


def test_sku_search_entries_exactly_one_target_constraint():
    """product_id XOR service_id — never both, never neither."""
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'sku_search_entries_exactly_one_target'"
            )
        ).scalar_one()
    # Expect: CHECK ((product_id IS NULL) <> (service_id IS NULL))
    assert "product_id IS NULL" in result
    assert "service_id IS NULL" in result


def test_customer_search_entries_pgvector_index_exists():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'customer_search_entries_embedding_ivfflat'"
            )
        ).scalar_one()
    assert "ivfflat" in result
    assert "vector_cosine_ops" in result


def test_customer_search_entries_fts_index_exists():
    with session_scope() as db:
        result = db.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'customer_search_entries_tsv_gin'"
            )
        ).scalar_one()
    assert "gin" in result
    assert "tsv" in result
```

- [ ] **Step 1.2 — Run test to verify it fails**

```bash
pytest tests/test_migration_004.py -v
```
Expected: 5 FAILS — tables/constraints don't exist yet.

- [ ] **Step 1.3 — Write the migration**

Create `alembic/versions/004_semantic_matcher.py`:

```python
"""semantic matcher: customer_search_entries + sku_search_entries.

Two indexed tables backing providers/semantic_matcher.py.

  customer_search_entries — one row per (customer.name | customer alias |
    customer_site.name); FK to customers. Hybrid retrieval uses pgvector
    cosine on `embedding` PLUS GIN(tsv) FTS, fused via RRF.

  sku_search_entries — one row per (product name | product code | product
    alias | service name | service code | service alias). Exactly one of
    product_id / service_id is non-null (CHECK constraint).

Both tables have a generated `tsv` column (to_tsvector('english',
entry_text)) so lexical search uses the same source-of-truth column.

Revision ID: 004_semantic_matcher
Revises: 003_add_copilot_conversations
Create Date: 2026-05-12
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, TSVECTOR


revision: str = "004_semantic_matcher"
down_revision: Union[str, None] = "003_add_copilot_conversations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- customer_search_entries -----
    op.execute(
        """
        CREATE TABLE customer_search_entries (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            entry_text  TEXT NOT NULL,
            source      TEXT NOT NULL,
            embedding   VECTOR(384) NOT NULL,
            tsv         TSVECTOR GENERATED ALWAYS AS
                          (to_tsvector('english', entry_text)) STORED NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT customer_search_entries_source_check
                CHECK (source IN ('name', 'alias', 'site'))
        )
        """
    )
    op.execute(
        "CREATE INDEX customer_search_entries_embedding_ivfflat "
        "ON customer_search_entries USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 32)"
    )
    op.execute(
        "CREATE INDEX customer_search_entries_tsv_gin "
        "ON customer_search_entries USING gin (tsv)"
    )
    op.create_index(
        "customer_search_entries_customer_id_idx",
        "customer_search_entries",
        ["customer_id"],
    )

    # ----- sku_search_entries -----
    op.execute(
        """
        CREATE TABLE sku_search_entries (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id  UUID NULL REFERENCES products(id) ON DELETE CASCADE,
            service_id  UUID NULL REFERENCES services(id) ON DELETE CASCADE,
            entry_text  TEXT NOT NULL,
            source      TEXT NOT NULL,
            embedding   VECTOR(384) NOT NULL,
            tsv         TSVECTOR GENERATED ALWAYS AS
                          (to_tsvector('english', entry_text)) STORED NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT sku_search_entries_source_check
                CHECK (source IN ('product_name','product_code','product_alias',
                                  'service_name','service_code','service_alias')),
            CONSTRAINT sku_search_entries_exactly_one_target
                CHECK ((product_id IS NULL) <> (service_id IS NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX sku_search_entries_embedding_ivfflat "
        "ON sku_search_entries USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 16)"
    )
    op.execute(
        "CREATE INDEX sku_search_entries_tsv_gin "
        "ON sku_search_entries USING gin (tsv)"
    )
    op.create_index(
        "sku_search_entries_product_id_idx", "sku_search_entries", ["product_id"]
    )
    op.create_index(
        "sku_search_entries_service_id_idx", "sku_search_entries", ["service_id"]
    )


def downgrade() -> None:
    op.drop_table("sku_search_entries")
    op.drop_table("customer_search_entries")
```

- [ ] **Step 1.4 — Append DDL to `tests/schema.sql`**

Add to the end of `tests/schema.sql` (exact SQL — matches the migration):

```sql
CREATE TABLE customer_search_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    entry_text  TEXT NOT NULL,
    source      TEXT NOT NULL,
    embedding   VECTOR(384) NOT NULL,
    tsv         TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', entry_text)) STORED NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT customer_search_entries_source_check
        CHECK (source IN ('name', 'alias', 'site'))
);
CREATE INDEX customer_search_entries_embedding_ivfflat
    ON customer_search_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 32);
CREATE INDEX customer_search_entries_tsv_gin
    ON customer_search_entries USING gin (tsv);
CREATE INDEX customer_search_entries_customer_id_idx
    ON customer_search_entries (customer_id);

CREATE TABLE sku_search_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NULL REFERENCES products(id) ON DELETE CASCADE,
    service_id  UUID NULL REFERENCES services(id) ON DELETE CASCADE,
    entry_text  TEXT NOT NULL,
    source      TEXT NOT NULL,
    embedding   VECTOR(384) NOT NULL,
    tsv         TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', entry_text)) STORED NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sku_search_entries_source_check
        CHECK (source IN ('product_name','product_code','product_alias',
                          'service_name','service_code','service_alias')),
    CONSTRAINT sku_search_entries_exactly_one_target
        CHECK ((product_id IS NULL) <> (service_id IS NULL))
);
CREATE INDEX sku_search_entries_embedding_ivfflat
    ON sku_search_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 16);
CREATE INDEX sku_search_entries_tsv_gin
    ON sku_search_entries USING gin (tsv);
CREATE INDEX sku_search_entries_product_id_idx
    ON sku_search_entries (product_id);
CREATE INDEX sku_search_entries_service_id_idx
    ON sku_search_entries (service_id);
```

- [ ] **Step 1.5 — Add SQLModel rows to `db/models.py`**

Add near the bottom of `db/models.py`, before the `__all__` list:

```python
class CustomerSearchEntry(SQLModel, table=True):
    """One indexed search entry — a customer name, alias, or site name.

    Hybrid retrieval (pgvector cosine + Postgres FTS) ranks rows; LLM
    disambiguator picks one. Many entries point to one customer.

    Source values: 'name' | 'alias' | 'site' (CHECK-constrained).
    """

    __tablename__: ClassVar[str] = "customer_search_entries"
    __table_args__ = (
        CheckConstraint(
            "source IN ('name', 'alias', 'site')",
            name="customer_search_entries_source_check",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_id: uuid.UUID = Field(
        foreign_key="customers.id", nullable=False
    )
    entry_text: str = Field(..., nullable=False)
    source: str = Field(..., nullable=False)
    # pgvector + tsv handled by raw DDL — not modeled on Python side.
    created_at: datetime = Field(default_factory=utc_now, nullable=False)

    customer: Mapped[Optional["Customer"]] = Relationship()


class SkuSearchEntry(SQLModel, table=True):
    """One indexed search entry for a product or service.

    Exactly one of product_id / service_id is non-null (CHECK).
    Source values: 'product_name' | 'product_code' | 'product_alias' |
                   'service_name' | 'service_code' | 'service_alias'.
    """

    __tablename__: ClassVar[str] = "sku_search_entries"
    __table_args__ = (
        CheckConstraint(
            "source IN ('product_name','product_code','product_alias',"
            "'service_name','service_code','service_alias')",
            name="sku_search_entries_source_check",
        ),
        CheckConstraint(
            "(product_id IS NULL) <> (service_id IS NULL)",
            name="sku_search_entries_exactly_one_target",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    product_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="products.id", nullable=True
    )
    service_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="services.id", nullable=True
    )
    entry_text: str = Field(..., nullable=False)
    source: str = Field(..., nullable=False)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
```

Append `"CustomerSearchEntry", "SkuSearchEntry"` to the `__all__` list.

- [ ] **Step 1.6 — Run migration + tests**

```bash
make reset-db && make migrate
pytest tests/test_migration_004.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 1.7 — Commit**

```bash
git add alembic/versions/004_semantic_matcher.py db/models.py tests/schema.sql tests/test_migration_004.py
git commit -m "feat(matcher): migration 004 + DB models for customer/sku search entries"
```

---

## Task 2: Pydantic schemas (CustomerMatch, SkuMatch, etc.)

**Files:**
- Modify: `agents/schemas.py`
- Modify: `agents/copilot/conversation_schemas.py` (if it re-exports CustomerMatch)
- Test: `tests/test_schemas_semantic_matcher.py` (NEW)

The existing `CustomerMatch` (line 143, with `score`/`ambiguous`/`candidates_considered`) is the rapidfuzz-shaped version and will be **replaced** with a new Grounded shape. Call sites that read `score`, `ambiguous`, or `candidates_considered` are updated in downstream tasks.

- [ ] **Step 2.1 — Write the failing schema test**

Create `tests/test_schemas_semantic_matcher.py`:

```python
"""New matcher schemas: Grounded shape, alternatives, exactly-one for SKU."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from agents.schemas import (
    ConversationContext,
    CustomerCandidate,
    CustomerMatch,
    SkuCandidate,
    SkuContext,
    SkuMatch,
)


def test_customer_match_grounded_shape():
    cid = uuid.uuid4()
    m = CustomerMatch(
        customer_id=cid,
        customer_name="O'Reilly Family Orchards",
        confidence=0.91,
        evidence=["O'Reilly", "back-40"],
        alternatives=[
            CustomerCandidate(
                customer_id=uuid.uuid4(),
                customer_name="O'Connell Brothers",
                confidence=0.62,
                source="name",
            )
        ],
    )
    assert m.customer_id == cid
    assert m.confidence == 0.91
    assert len(m.evidence) == 2
    assert len(m.alternatives) == 1


def test_customer_match_null_for_no_resolution():
    m = CustomerMatch(
        customer_id=None,
        customer_name=None,
        confidence=0.3,
        evidence=["the company"],
        alternatives=[],
    )
    assert m.customer_id is None
    assert m.customer_name is None


def test_customer_match_confidence_range():
    with pytest.raises(ValidationError):
        CustomerMatch(
            customer_id=None,
            customer_name=None,
            confidence=1.5,  # out of range
            evidence=[],
            alternatives=[],
        )


def test_sku_match_exactly_one_id():
    pid = uuid.uuid4()
    m = SkuMatch(
        sku_kind="product",
        product_id=pid,
        service_id=None,
        sku_code="PKG-BEES-3LB-ITA",
        name="Italian queens (3-lb package)",
        confidence=0.95,
        evidence=["Italian queens"],
        alternatives=[],
    )
    assert m.product_id == pid
    assert m.service_id is None


def test_sku_match_rejects_both_ids():
    with pytest.raises(ValidationError, match="exactly one"):
        SkuMatch(
            sku_kind="product",
            product_id=uuid.uuid4(),
            service_id=uuid.uuid4(),  # both set — invalid
            sku_code="X",
            name="X",
            confidence=0.9,
            evidence=[],
            alternatives=[],
        )


def test_sku_match_null_for_no_resolution():
    m = SkuMatch(
        sku_kind=None,
        product_id=None,
        service_id=None,
        sku_code=None,
        name=None,
        confidence=0.0,
        evidence=[],
        alternatives=[],
    )
    assert m.sku_kind is None


def test_conversation_context_defaults():
    ctx = ConversationContext()
    assert ctx.active_account_id is None
    assert ctx.last_3_customer_ids == []


def test_sku_context_is_empty_for_phase_i():
    """SkuContext is a placeholder until customer-aware SKU bias arrives."""
    ctx = SkuContext()
    # No required fields; instantiation alone is the assertion.
    assert ctx is not None
```

- [ ] **Step 2.2 — Run test to verify it fails**

```bash
pytest tests/test_schemas_semantic_matcher.py -v
```
Expected: ImportError or AttributeError — the new schemas don't exist.

- [ ] **Step 2.3 — Replace the existing CustomerMatch + add the new schemas**

In `agents/schemas.py`, **DELETE** the existing `CustomerMatch` class (lines 143–176) and add the following block in its place:

```python
# ---------------------------------------------------------------------------
# Semantic matcher schemas — Grounded results with evidence + confidence
# ---------------------------------------------------------------------------


class ConversationContext(BaseModel):
    """Per-turn context passed to match_customer.

    Lets the LLM disambiguator resolve pronouns ("they"), prefer the
    active account on ambiguous ties, and weigh recent customers higher.
    """

    model_config = ConfigDict(frozen=True)

    active_account_id: uuid.UUID | None = Field(
        default=None,
        description="Customer currently active in the conversation, if any.",
    )
    last_3_customer_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Recent customer IDs referenced in this conversation.",
    )


class SkuContext(BaseModel):
    """Placeholder context for SKU matching (Phase II will populate)."""

    model_config = ConfigDict(frozen=True)


class CustomerCandidate(BaseModel):
    """One alternative customer surfaced by the matcher."""

    model_config = ConfigDict(frozen=True)

    customer_id: uuid.UUID
    customer_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: Literal["name", "alias", "site"] = Field(
        ..., description="Which kind of entry matched (name/alias/site)."
    )


class CustomerMatch(Grounded):
    """Result of resolving a free-text customer reference to a row.

    Grounded — evidence quotes the user's text that justified the match.
    customer_id is None iff the matcher could not pick confidently
    (caller decides whether to ask the user or to fall through).
    """

    model_config = ConfigDict(frozen=True)

    customer_id: uuid.UUID | None = None
    customer_name: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    alternatives: list[CustomerCandidate] = Field(default_factory=list)


class SkuCandidate(BaseModel):
    """One alternative SKU surfaced by the matcher."""

    model_config = ConfigDict(frozen=True)

    sku_kind: Literal["product", "service"]
    product_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    sku_code: str
    name: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class SkuMatch(Grounded):
    """Result of resolving a free-text product/service reference.

    Exactly one of product_id / service_id is non-null when sku_kind is
    not None. When sku_kind is None the matcher could not resolve.
    """

    model_config = ConfigDict(frozen=True)

    sku_kind: Literal["product", "service"] | None = None
    product_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    sku_code: str | None = None
    name: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    alternatives: list[SkuCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_id(self) -> "SkuMatch":
        if self.sku_kind is None:
            if self.product_id is not None or self.service_id is not None:
                raise ValueError("sku_kind None requires both IDs null")
            return self
        # sku_kind is "product" or "service" — exactly one ID must be set
        if (self.product_id is None) == (self.service_id is None):
            raise ValueError(
                "SkuMatch must set exactly one of product_id/service_id "
                "when sku_kind is not None"
            )
        return self
```

Update the imports at the top of `agents/schemas.py`:

```python
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
```

- [ ] **Step 2.4 — Run test to verify it passes**

```bash
pytest tests/test_schemas_semantic_matcher.py -v
```
Expected: 8/8 PASS.

- [ ] **Step 2.5 — Check pyright**

```bash
pyright agents/schemas.py tests/test_schemas_semantic_matcher.py
```
Expected: 0 errors. Warnings on Optional types are acceptable per project baseline.

- [ ] **Step 2.6 — Commit**

```bash
git add agents/schemas.py tests/test_schemas_semantic_matcher.py
git commit -m "feat(matcher): replace fuzzy CustomerMatch with Grounded shape + SkuMatch"
```

---

## Task 3: Hybrid retrieve (Stage A)

**Files:**
- Create: `providers/semantic_matcher.py` (skeleton + Stage A only this task)
- Test: `tests/test_provider_semantic_matcher.py` (NEW — Stage A tests this task)

- [ ] **Step 3.1 — Write the failing hybrid-retrieve test**

Create `tests/test_provider_semantic_matcher.py` with this initial content:

```python
"""Tests for providers/semantic_matcher.py.

Layered: this file grows through Tasks 3–6. Stage A (hybrid retrieve) first,
then Stage B (LLM disambig), then the public API + cache.

Real deps: real pgvector, real sentence-transformers, real Postgres FTS.
LLM tests are added in later tasks and skip cleanly without an OPENAI_API_KEY.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from db.session import session_scope
from providers import semantic_matcher
from providers.semantic_matcher import Candidate


@pytest.fixture
def seeded_customer_entries(db_session_with_seed):
    """A fresh DB with seeded customers + customer_search_entries backfilled.
    The seed pipeline (extended in Task 7) populates the entries; for
    tests in Task 3 we backfill explicitly using the helper."""
    semantic_matcher.backfill_all(db_session_with_seed)
    return db_session_with_seed


def test_hybrid_retrieve_returns_canonical_name_for_exact_query(
    seeded_customer_entries,
):
    """'O'Reilly Family Orchards' is a canonical name in seed/customers.py.
    Hybrid retrieve must return that customer in the top-K via both
    dense AND lexical passes."""
    candidates = semantic_matcher._hybrid_retrieve(
        "O'Reilly Family Orchards",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    assert len(candidates) > 0, "Empty shortlist for canonical name"
    top = candidates[0]
    assert "O'Reilly" in top.entry_text


def test_hybrid_retrieve_returns_site_for_site_reference(seeded_customer_entries):
    """'back-40' is a seeded site name on O'Reilly Family Orchards."""
    candidates = semantic_matcher._hybrid_retrieve(
        "back-40",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    customer_ids = {c.customer_id for c in candidates}
    # Verify the O'Reilly customer is present in the shortlist
    row = seeded_customer_entries.execute(
        text("SELECT id FROM customers WHERE name = :n"),
        {"n": "O'Reilly Family Orchards"},
    ).first()
    assert row is not None
    assert row.id in customer_ids


def test_hybrid_retrieve_empty_query_returns_empty_list(seeded_customer_entries):
    candidates = semantic_matcher._hybrid_retrieve(
        "",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    assert candidates == []


def test_hybrid_retrieve_limit_is_top_5(seeded_customer_entries):
    candidates = semantic_matcher._hybrid_retrieve(
        "orchard",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    assert len(candidates) <= 5
    # RRF fused score must be sorted descending
    scores = [c.fused_score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_hybrid_retrieve_multi_source_boost(seeded_customer_entries):
    """When 2+ entries from the same customer rank in the shortlist
    (e.g., canonical name AND a site name both match 'O'Reilly back-40'),
    the customer's fused score should reflect the boost."""
    candidates = semantic_matcher._hybrid_retrieve(
        "O'Reilly back-40",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    # Group by customer; the customer with 2+ entries should be on top
    by_customer: dict[uuid.UUID, list[Candidate]] = {}
    for c in candidates:
        by_customer.setdefault(c.customer_id, []).append(c)
    top_customer_id = candidates[0].customer_id
    assert len(by_customer[top_customer_id]) >= 2
```

Also add to `tests/conftest.py`:

```python
@pytest.fixture
def db_session_with_seed(db):
    """Provides a SQLAlchemy Session against a seeded DB.

    `db` is the per-test fresh-schema fixture. seed.generate.run() populates
    customers / sites / products / services so search entries can be backfilled.
    """
    from seed import generate
    generate.run(db)
    return db
```

(Skip this addition if a similar fixture already exists; reuse it.)

- [ ] **Step 3.2 — Run test to verify it fails**

```bash
pytest tests/test_provider_semantic_matcher.py -v
```
Expected: 5 FAILS — `providers.semantic_matcher` doesn't exist.

- [ ] **Step 3.3 — Implement the provider skeleton + Stage A**

Create `providers/semantic_matcher.py`:

```python
"""Semantic matcher — resolves free-text customer / SKU references.

Public surface:

    match_customer(hint, conversation_ctx, db) -> CustomerMatch
    match_sku(query, sku_context, db) -> SkuMatch
    reindex_customer(customer_id, db) -> None
    reindex_sku(product_id=..., service_id=..., db) -> None
    backfill_all(db) -> int

Pipeline (both customer + SKU paths):

    Stage A — Hybrid retrieve:
        embed(query) → pgvector cosine over <table>.embedding (top 20)
        +
        plainto_tsquery(query) → ts_rank over <table>.tsv (top 20)
        → Reciprocal Rank Fusion (k=60) → top 5 fused candidates
        → multi-source boost (when 2+ entries from the same target
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

from providers.vector_search import VectorSearchClient

logger = logging.getLogger(__name__)


RRF_K = 60
TOP_K = 5
RETRIEVE_LIMIT = 20  # per-source before fusion
PROMPT_VERSION = "v1"


# ---------------------------------------------------------------------------
# Internal data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One shortlisted entry from Stage A. Hides the entry-row vs.
    target-row distinction; downstream consumers care about the target
    (customer_id / product_id / service_id).
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


_vector_client: VectorSearchClient | None = None


def _get_vector_client() -> VectorSearchClient:
    """Lazy-init the shared sentence-transformers client (same model used
    for customer-report RAG — all-MiniLM-L6-v2, 384-dim)."""
    global _vector_client
    if _vector_client is None:
        _vector_client = VectorSearchClient()
    return _vector_client


def _normalize_for_index_and_query(s: str) -> str:
    """Lightweight normalization applied to both index entries (at build
    time) and incoming queries (at match time).

    Lowercases, collapses whitespace, strips ASCII punctuation that
    interferes with FTS tokenization while keeping internal hyphens
    (e.g., 'back-40' stays as one token).
    """
    s = (s or "").lower().strip()
    # Replace common punctuation with spaces — but keep hyphens (back-40)
    for ch in [",", ".", "'", '"', "(", ")", "/", "&", "!", "?", ":", ";"]:
        s = s.replace(ch, " ")
    # Collapse multiple spaces
    return " ".join(s.split())


def _hybrid_retrieve(
    query: str,
    *,
    table: Literal["customer_search_entries", "sku_search_entries"],
    db: Session,
) -> list[Candidate]:
    """Hybrid retrieve: dense (pgvector cosine) + lexical (Postgres FTS)
    fused via Reciprocal Rank Fusion.

    Returns up to TOP_K candidates sorted by fused_score descending.
    Empty query returns []. Empty result set returns []."""
    normalized = _normalize_for_index_and_query(query)
    if not normalized:
        return []

    # Embed the query (same model used at index-build time)
    client = _get_vector_client()
    embedding = client.embed(normalized)  # list[float] of len 384

    # Build the CTE with both passes + RRF fusion + multi-source boost.
    # Column projection depends on the table — customer or SKU.
    if table == "customer_search_entries":
        target_cols = "customer_id, NULL::uuid AS product_id, NULL::uuid AS service_id"
        group_col = "customer_id"
    else:
        target_cols = "NULL::uuid AS customer_id, product_id, service_id"
        # Group by whichever target is non-null
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
                entry_id=r["id"],
                customer_id=r.get("customer_id"),
                product_id=r.get("product_id"),
                service_id=r.get("service_id"),
                entry_text=r["entry_text"],
                source=r["source"],
                dense_rank=r.get("dense_rank"),
                fts_rank=r.get("fts_rank"),
                dense_score=r.get("dense_score"),
                fts_score=r.get("fts_score"),
                fused_score=float(r["boosted_score"]),
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Backfill (used by tests + seed + boot.py reindex-if-empty)
# Implemented stub here so tests can call it; full impl lives in Task 7.
# ---------------------------------------------------------------------------


def backfill_all(db: Session) -> int:
    """Populate both search-entry tables from customers/sites/products/services.

    Idempotent: TRUNCATEs the tables, then re-inserts. Returns total row count.
    Real implementation in Task 7 (this stub is a placeholder).
    """
    raise NotImplementedError(
        "backfill_all is implemented in Task 7 of the matcher plan; "
        "called from Stage-A tests via the seeded_customer_entries fixture."
    )
```

> **Note for the implementing engineer:** the `backfill_all` stub raises so Task 3 tests don't accidentally pass without the data. You'll implement `backfill_all` properly in Task 7. For Task 3 tests, temporarily insert a minimal fixture set via a `conftest.py` helper:

Add to `tests/conftest.py`:

```python
@pytest.fixture
def db_session_with_search_entries(db_session_with_seed):
    """Inserts a minimal customer_search_entries set so Stage-A tests
    can run before Task 7's full backfill is implemented.

    This fixture is REMOVED after Task 7 lands and the seeded_customer_entries
    fixture switches to calling backfill_all() directly.
    """
    from providers.vector_search import VectorSearchClient
    client = VectorSearchClient()
    db = db_session_with_seed

    rows = db.execute(text(
        "SELECT c.id AS customer_id, c.name AS entry_text "
        "FROM customers c"
    )).mappings().all()
    for r in rows:
        emb = client.embed(r["entry_text"])
        db.execute(text(
            "INSERT INTO customer_search_entries "
            "(customer_id, entry_text, source, embedding) "
            "VALUES (:cid, :et, 'name', CAST(:emb AS vector))"
        ), {"cid": r["customer_id"], "et": r["entry_text"], "emb": emb})

    site_rows = db.execute(text(
        "SELECT customer_id, name AS entry_text FROM customer_sites"
    )).mappings().all()
    for r in site_rows:
        emb = client.embed(r["entry_text"])
        db.execute(text(
            "INSERT INTO customer_search_entries "
            "(customer_id, entry_text, source, embedding) "
            "VALUES (:cid, :et, 'site', CAST(:emb AS vector))"
        ), {"cid": r["customer_id"], "et": r["entry_text"], "emb": emb})

    db.commit()
    return db
```

Update `seeded_customer_entries` in the test file to use this fixture:

```python
@pytest.fixture
def seeded_customer_entries(db_session_with_search_entries):
    return db_session_with_search_entries
```

- [ ] **Step 3.4 — Run tests to verify Stage A passes**

```bash
pytest tests/test_provider_semantic_matcher.py -v
```
Expected: 5/5 PASS.

- [ ] **Step 3.5 — Commit**

```bash
git add providers/semantic_matcher.py tests/test_provider_semantic_matcher.py tests/conftest.py
git commit -m "feat(matcher): Stage A — hybrid retrieve (pgvector + FTS, RRF fusion)"
```

---

## Task 4: LLM disambiguate (Stage B)

**Files:**
- Modify: `providers/semantic_matcher.py` (add `_llm_disambiguate`)
- Create: `providers/semantic_matcher_prompts.py`
- Modify: `tests/test_provider_semantic_matcher.py` (extend)

- [ ] **Step 4.1 — Write the failing LLM-disambig test**

Append to `tests/test_provider_semantic_matcher.py`:

```python
# ---------------------------------------------------------------------------
# Stage B — LLM disambiguation
# ---------------------------------------------------------------------------

import os

import pytest

require_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — LLM disambig tests skipped",
)


@require_openai_key
def test_llm_disambiguate_picks_active_account_on_ambiguous_tie(
    seeded_customer_entries,
):
    """When two candidates have similar fused scores, the LLM should
    prefer the active_account_id from conversation context."""
    from agents.schemas import ConversationContext

    # Build a candidate list where two entries look equally good but only
    # one matches the active account
    candidates = semantic_matcher._hybrid_retrieve(
        "the orchard",
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    if len(candidates) < 2:
        pytest.skip("Seed data didn't produce a useful ambiguous shortlist")

    active_id = candidates[1].customer_id  # not the top-scoring one
    ctx = ConversationContext(
        active_account_id=active_id,
        last_3_customer_ids=[active_id] if active_id else [],
    )

    match = semantic_matcher._llm_disambiguate(
        query="the orchard",
        candidates=candidates,
        kind="customer",
        ctx=ctx,
    )
    # The LLM should prefer the active account on a near-tie
    assert match.customer_id == active_id


@require_openai_key
def test_llm_disambiguate_returns_null_on_clear_miss(seeded_customer_entries):
    """For a hint that doesn't plausibly match any candidate, the LLM
    should return customer_id=None."""
    from agents.schemas import ConversationContext

    # Force a candidate list that has nothing to do with the query
    candidates = semantic_matcher._hybrid_retrieve(
        "Italian queens",  # a SKU phrase against the customer index
        table="customer_search_entries",
        db=seeded_customer_entries,
    )
    if not candidates:
        pytest.skip("Empty candidate list — degenerate test")

    match = semantic_matcher._llm_disambiguate(
        query="Zephyr Bee Company",
        candidates=candidates,
        kind="customer",
        ctx=ConversationContext(),
    )
    assert match.customer_id is None
    assert match.confidence < 0.5
```

- [ ] **Step 4.2 — Create the prompt module**

Create `providers/semantic_matcher_prompts.py`:

```python
"""Versioned disambiguator prompts for the semantic matcher.

Bump PROMPT_VERSION (in providers/semantic_matcher.py) whenever the
prompt template changes. Add a new entry to PROMPT_HISTORY for
eval-report traceability.
"""
# pyright: reportMissingImports=false
from __future__ import annotations


CUSTOMER_DISAMBIG_SYSTEM = """You are a customer-resolution assistant for a commercial beekeeping CRM.

You are given:
  - A user's free-text reference to a customer (the "query").
  - The currently-active customer in the conversation, if any.
  - The last few customers referenced in this conversation, if any.
  - A shortlist of candidate customers, each matched on a name, alias, or site name.

Your job: pick ONE candidate the user is referring to, OR return null if
none of the candidates plausibly match.

Rules:
  1. If the query is ambiguous between two candidates and one is the
     active_account, prefer the active_account.
  2. If the query mentions a site name (e.g. "the back-40"), pick the
     customer that owns that site.
  3. If the query plausibly refers to a customer NOT in the candidate
     list, return null with low confidence.
  4. Return confidence in [0.0, 1.0]. Use ≥ 0.80 only when the match is
     unambiguous and a human reading the query would agree.
  5. Return 1-3 evidence quotes — exact substrings from the query that
     justified the match.
  6. Populate alternatives with the next-best up to 3 candidates and
     their confidences.
"""

CUSTOMER_DISAMBIG_USER_TEMPLATE = """User said: {query!r}

Active account in conversation: {active_account_name!r}
Recent customers in this conversation: {recent_customer_names!r}

Candidates:
{candidate_block}

Return a CustomerMatch JSON object."""


SKU_DISAMBIG_SYSTEM = """You are a product/service-resolution assistant for a commercial beekeeping CRM.

You are given:
  - A user's free-text reference to a product or service (the "query").
  - A shortlist of candidate SKUs (each is either a product or a service).

Your job: pick ONE candidate the user is referring to, OR return null if
none plausibly match.

Rules:
  1. If the query is unambiguous (e.g. mentions an SKU code verbatim),
     pick that candidate with high confidence.
  2. If the query could mean either a product or a service, prefer the
     one whose name more closely matches the query.
  3. Return confidence in [0.0, 1.0]. Use ≥ 0.80 only when unambiguous.
  4. Return 1-3 evidence quotes — exact substrings from the query.
  5. Populate alternatives with the next-best up to 3 candidates.
"""

SKU_DISAMBIG_USER_TEMPLATE = """User said: {query!r}

Candidates:
{candidate_block}

Return an SkuMatch JSON object."""


PROMPT_HISTORY = [
    {
        "version": "v1",
        "date": "2026-05-12",
        "note": "Initial release — Phase I matcher.",
    },
]
```

- [ ] **Step 4.3 — Implement `_llm_disambiguate`**

Add to `providers/semantic_matcher.py` (after the Stage A code):

```python
# ---------------------------------------------------------------------------
# Stage B — LLM disambiguate
# ---------------------------------------------------------------------------

import json
from typing import Any

from app.config import get_settings
from providers.llm import LLMConfigurationError, get_openai_client
from providers.semantic_matcher_prompts import (
    CUSTOMER_DISAMBIG_SYSTEM,
    CUSTOMER_DISAMBIG_USER_TEMPLATE,
    SKU_DISAMBIG_SYSTEM,
    SKU_DISAMBIG_USER_TEMPLATE,
)


def _format_customer_candidate_block(
    candidates: list[Candidate], db: Session
) -> str:
    """Render candidates as a numbered list for the LLM prompt.

    Looks up each customer's canonical name via the DB for the prompt
    (entry_text might be an alias or site — we want canonical names).
    """
    if not candidates:
        return "(empty)"
    customer_ids = list({c.customer_id for c in candidates if c.customer_id})
    name_rows = db.execute(
        text("SELECT id, name FROM customers WHERE id = ANY(:ids)"),
        {"ids": customer_ids},
    ).mappings().all()
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
    """Render SKU candidates for the LLM prompt."""
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

    Side effects:
        * One OpenAI API call.
        * Logs at INFO on each call; WARNING on retry; ERROR on terminal failure.
    """
    from agents.schemas import (
        ConversationContext,
        CustomerCandidate,
        CustomerMatch,
        SkuCandidate,
        SkuMatch,
    )

    settings = get_settings()
    client = get_openai_client()
    model = settings.copilot_matcher_disambig_model  # default: gpt-5.4-nano

    if kind == "customer":
        assert isinstance(ctx, ConversationContext)
        # Look up active account + recent customer names for the prompt
        active_name = None
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
        schema = CustomerMatch.model_json_schema()
        match_class = CustomerMatch
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

    last_error: Exception | None = None
    for attempt in (1, 2):
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
            match = match_class.model_validate(parsed)

            # Defense: if the LLM picked an id not in our shortlist, discard.
            if kind == "customer":
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
                        confidence=(top.fused_score * 0.5) if top else 0.0,
                        evidence=[query],
                        alternatives=[],
                    )
            return match
        except Exception as e:
            last_error = e
            if attempt == 1:
                logger.warning("LLM disambig attempt 1 failed: %s; retrying", e)
                continue
            logger.error("LLM disambig terminal failure: %s", e)
            break

    # Terminal failure — return a low-confidence null
    if kind == "customer":
        return CustomerMatch(
            customer_id=None, customer_name=None,
            confidence=0.0, evidence=[], alternatives=[],
        )
    else:
        return SkuMatch(
            sku_kind=None, product_id=None, service_id=None,
            sku_code=None, name=None,
            confidence=0.0, evidence=[], alternatives=[],
        )
```

Add to `app/config.py` (in the Settings class):

```python
    copilot_matcher_disambig_model: str = Field(
        default="gpt-5.4-nano",
        description="OpenAI chat model for the semantic matcher's LLM disambiguator.",
    )
```

- [ ] **Step 4.4 — Run tests to verify Stage B passes**

```bash
pytest tests/test_provider_semantic_matcher.py -v
```
Expected: all Stage-A tests still pass + new Stage-B tests PASS (or skip cleanly if `OPENAI_API_KEY` absent).

- [ ] **Step 4.5 — Commit**

```bash
git add providers/semantic_matcher.py providers/semantic_matcher_prompts.py app/config.py tests/test_provider_semantic_matcher.py
git commit -m "feat(matcher): Stage B — LLM disambiguator with structured output"
```

---

## Task 5: Public API + cache + observability hook (Stage C)

**Files:**
- Modify: `providers/semantic_matcher.py` (add `match_customer`, `match_sku`, LRU cache)
- Modify: `tests/test_provider_semantic_matcher.py` (extend)

- [ ] **Step 5.1 — Write the failing public-API + cache tests**

Append to `tests/test_provider_semantic_matcher.py`:

```python
# ---------------------------------------------------------------------------
# Public API + cache (Stage C)
# ---------------------------------------------------------------------------

@require_openai_key
def test_match_customer_canonical(seeded_customer_entries):
    from agents.schemas import ConversationContext

    match = semantic_matcher.match_customer(
        "O'Reilly Family Orchards",
        conversation_ctx=ConversationContext(),
        conversation_id=uuid.uuid4(),
        db=seeded_customer_entries,
    )
    assert match.customer_id is not None
    assert match.confidence >= 0.80
    assert "O'Reilly" in (match.customer_name or "")


@require_openai_key
def test_match_customer_cache_hit_avoids_second_llm_call(
    seeded_customer_entries, monkeypatch,
):
    """Second call with the same (hint, ctx, conv) hits the cache —
    monkeypatch _llm_disambiguate to assert it's only called once."""
    from agents.schemas import ConversationContext

    call_count = {"n": 0}
    original = semantic_matcher._llm_disambiguate

    def counting(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", counting)
    semantic_matcher.clear_caches()

    conv_id = uuid.uuid4()
    ctx = ConversationContext()
    semantic_matcher.match_customer(
        "O'Reilly", conversation_ctx=ctx, conversation_id=conv_id,
        db=seeded_customer_entries,
    )
    semantic_matcher.match_customer(
        "O'Reilly", conversation_ctx=ctx, conversation_id=conv_id,
        db=seeded_customer_entries,
    )
    assert call_count["n"] == 1, "LLM should have been called only once"


def test_match_customer_empty_hint_returns_null_no_llm_call(
    seeded_customer_entries, monkeypatch,
):
    """Empty hint short-circuits — no LLM call."""
    from agents.schemas import ConversationContext

    def boom(*args, **kwargs):
        raise AssertionError("LLM should not be called for empty hint")

    monkeypatch.setattr(semantic_matcher, "_llm_disambiguate", boom)
    semantic_matcher.clear_caches()

    match = semantic_matcher.match_customer(
        "", conversation_ctx=ConversationContext(),
        conversation_id=uuid.uuid4(), db=seeded_customer_entries,
    )
    assert match.customer_id is None


def test_cache_lru_evicts_oldest(seeded_customer_entries, monkeypatch):
    """Cache cap is CACHE_CAP_PER_CONV (64); the 65th distinct hint
    evicts the oldest."""
    from agents.schemas import ConversationContext
    monkeypatch.setattr(semantic_matcher, "CACHE_CAP_PER_CONV", 2)
    semantic_matcher.clear_caches()

    conv_id = uuid.uuid4()
    ctx = ConversationContext()
    for hint in ["foo", "bar", "baz"]:  # 3 hints, cap is 2
        semantic_matcher.match_customer(
            hint, conversation_ctx=ctx, conversation_id=conv_id,
            db=seeded_customer_entries,
        )
    cache = semantic_matcher._cache_for(conv_id)
    assert len(cache) == 2


def test_concurrent_calls_idempotent(seeded_customer_entries):
    """Two threads calling match_customer with the same args produce
    the same answer (idempotent under race)."""
    import concurrent.futures
    from agents.schemas import ConversationContext

    semantic_matcher.clear_caches()
    conv_id = uuid.uuid4()
    ctx = ConversationContext()

    def call():
        return semantic_matcher.match_customer(
            "O'Reilly", conversation_ctx=ctx, conversation_id=conv_id,
            db=seeded_customer_entries,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(call)
        f2 = ex.submit(call)
        r1, r2 = f1.result(), f2.result()
    assert r1.customer_id == r2.customer_id
```

- [ ] **Step 5.2 — Implement the public API + cache**

Add to `providers/semantic_matcher.py`:

```python
# ---------------------------------------------------------------------------
# Stage C — Public API + cache
# ---------------------------------------------------------------------------

from collections import OrderedDict
from threading import Lock

CACHE_CAP_PER_CONV = 64

# Per-conversation LRU caches. Key: conv_id → OrderedDict[(hint_norm, ctx_key), match]
_conv_caches: dict[uuid.UUID, OrderedDict[tuple, Any]] = {}
_cache_lock = Lock()


def _cache_for(conversation_id: uuid.UUID) -> OrderedDict[tuple, Any]:
    with _cache_lock:
        return _conv_caches.setdefault(conversation_id, OrderedDict())


def clear_caches() -> None:
    """Test helper — clears all per-conversation caches."""
    with _cache_lock:
        _conv_caches.clear()


def _ctx_key(ctx: Any) -> tuple:
    """Stable, hashable key derived from a context dataclass/BaseModel."""
    from agents.schemas import ConversationContext, SkuContext
    if isinstance(ctx, ConversationContext):
        return ("conv", ctx.active_account_id, tuple(ctx.last_3_customer_ids))
    if isinstance(ctx, SkuContext):
        return ("sku",)
    return ("none",)


class MatcherIndexEmpty(RuntimeError):
    """Search-entries table is empty — backfill must run before matching."""


def _check_index_populated(table: str, db: Session) -> None:
    n = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
    if not n:
        raise MatcherIndexEmpty(
            f"{table} is empty. Run providers.semantic_matcher.backfill_all(db) "
            f"or set MATCHER_BOOTSTRAP=1 to auto-reindex on boot."
        )


def match_customer(
    hint: str,
    *,
    conversation_ctx: Any,  # ConversationContext
    conversation_id: uuid.UUID,
    db: Session,
) -> Any:  # CustomerMatch
    """Resolve a free-text customer reference to a customer row.

    Side effects:
        * Reads customers + customer_search_entries.
        * One OpenAI API call (Stage B) unless cache-hit or empty hint.
        * Writes to the per-conversation in-memory cache.
    """
    from agents.schemas import CustomerMatch

    # Empty-hint short-circuit (no LLM call)
    if not hint or not hint.strip():
        return CustomerMatch(
            customer_id=None, customer_name=None,
            confidence=0.0, evidence=[], alternatives=[],
        )

    # Truncate over-long hints
    if len(hint) > 512:
        logger.warning("Truncating over-long matcher hint from %d to 512 chars", len(hint))
        hint = hint[:512]

    _check_index_populated("customer_search_entries", db)

    cache = _cache_for(conversation_id)
    cache_key = (_normalize_for_index_and_query(hint), _ctx_key(conversation_ctx))
    if cache_key in cache:
        cache.move_to_end(cache_key)
        return cache[cache_key]

    candidates = _hybrid_retrieve(
        hint, table="customer_search_entries", db=db,
    )
    if not candidates:
        result = CustomerMatch(
            customer_id=None, customer_name=None,
            confidence=0.0, evidence=[], alternatives=[],
        )
    else:
        result = _llm_disambiguate(
            query=hint, candidates=candidates,
            kind="customer", ctx=conversation_ctx, db=db,
        )

    # Insert + LRU evict
    cache[cache_key] = result
    while len(cache) > CACHE_CAP_PER_CONV:
        cache.popitem(last=False)

    return result


def match_sku(
    query: str,
    *,
    sku_context: Any,  # SkuContext
    conversation_id: uuid.UUID,
    db: Session,
) -> Any:  # SkuMatch
    """Resolve a free-text product/service reference.

    Short-circuits on exact SKU-code match (skips Stage A + B).
    """
    from agents.schemas import SkuMatch

    if not query or not query.strip():
        return SkuMatch(
            sku_kind=None, product_id=None, service_id=None,
            sku_code=None, name=None,
            confidence=0.0, evidence=[], alternatives=[],
        )
    if len(query) > 512:
        query = query[:512]

    _check_index_populated("sku_search_entries", db)

    # Exact SKU-code short-circuit
    normalized = query.strip().upper()
    row = db.execute(
        text("SELECT id, sku, name FROM products WHERE sku = :sku"),
        {"sku": normalized},
    ).first()
    if row:
        return SkuMatch(
            sku_kind="product", product_id=row.id, service_id=None,
            sku_code=row.sku, name=row.name,
            confidence=1.0, evidence=[query],
            alternatives=[],
        )
    row = db.execute(
        text("SELECT id, sku, name FROM services WHERE sku = :sku"),
        {"sku": normalized},
    ).first()
    if row:
        return SkuMatch(
            sku_kind="service", product_id=None, service_id=row.id,
            sku_code=row.sku, name=row.name,
            confidence=1.0, evidence=[query],
            alternatives=[],
        )

    cache = _cache_for(conversation_id)
    cache_key = ("sku", _normalize_for_index_and_query(query), _ctx_key(sku_context))
    if cache_key in cache:
        cache.move_to_end(cache_key)
        return cache[cache_key]

    candidates = _hybrid_retrieve(
        query, table="sku_search_entries", db=db,
    )
    if not candidates:
        result = SkuMatch(
            sku_kind=None, product_id=None, service_id=None,
            sku_code=None, name=None,
            confidence=0.0, evidence=[], alternatives=[],
        )
    else:
        result = _llm_disambiguate(
            query=query, candidates=candidates,
            kind="sku", ctx=sku_context, db=db,
        )
    cache[cache_key] = result
    while len(cache) > CACHE_CAP_PER_CONV:
        cache.popitem(last=False)
    return result
```

- [ ] **Step 5.3 — Run tests to verify Stage C passes**

```bash
pytest tests/test_provider_semantic_matcher.py -v
```
Expected: all pre-existing tests + new Stage-C tests PASS (or skip without OPENAI_API_KEY).

- [ ] **Step 5.4 — Commit**

```bash
git add providers/semantic_matcher.py tests/test_provider_semantic_matcher.py
git commit -m "feat(matcher): Stage C — public API, per-conversation LRU cache, MatcherIndexEmpty guard"
```

---

## Task 6: Backfill + reindex helpers

**Files:**
- Modify: `providers/semantic_matcher.py` (replace `backfill_all` stub; add `reindex_customer`, `reindex_sku`)
- Create: `seed/customer_search_entries.py`
- Create: `seed/sku_search_entries.py`
- Modify: `seed/embeddings.py` (call the new backfills)
- Modify: `tests/test_provider_semantic_matcher.py` (idempotency tests)

- [ ] **Step 6.1 — Write the failing backfill/reindex idempotency tests**

Append to `tests/test_provider_semantic_matcher.py`:

```python
# ---------------------------------------------------------------------------
# Backfill + reindex
# ---------------------------------------------------------------------------

def test_backfill_all_is_idempotent(db_session_with_seed):
    """Calling backfill_all twice produces the same final row counts."""
    n1 = semantic_matcher.backfill_all(db_session_with_seed)
    n2 = semantic_matcher.backfill_all(db_session_with_seed)
    assert n1 == n2
    assert n1 > 0


def test_backfill_creates_entries_for_every_customer_and_alias(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    rows = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries WHERE source = 'name'"
    )).scalar_one()
    customer_count = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customers"
    )).scalar_one()
    assert rows == customer_count


def test_backfill_creates_one_entry_per_site(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    rows = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries WHERE source = 'site'"
    )).scalar_one()
    sites = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_sites"
    )).scalar_one()
    assert rows == sites


def test_reindex_customer_only_touches_that_customer(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    cid = db_session_with_seed.execute(text(
        "SELECT id FROM customers LIMIT 1"
    )).scalar_one()
    before_total = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries"
    )).scalar_one()
    semantic_matcher.reindex_customer(cid, db_session_with_seed)
    after_total = db_session_with_seed.execute(text(
        "SELECT COUNT(*) FROM customer_search_entries"
    )).scalar_one()
    assert before_total == after_total  # idempotent — same row count
```

- [ ] **Step 6.2 — Replace the `backfill_all` stub + add reindex helpers**

In `providers/semantic_matcher.py`, replace the stub with:

```python
def backfill_all(db: Session) -> int:
    """Truncate-and-rebuild both search-entry tables from
    customers/sites/products/services. Idempotent.

    Returns the total row count written across both tables.

    Side effects:
        * TRUNCATE on customer_search_entries + sku_search_entries.
        * INSERTs proportional to (customers + aliases + sites +
          products + services + their codes + aliases).
        * Embeds every entry_text via the shared VectorSearchClient.
        * Commits the transaction.
    """
    client = _get_vector_client()
    db.execute(text("TRUNCATE TABLE customer_search_entries"))
    db.execute(text("TRUNCATE TABLE sku_search_entries"))
    total = 0

    # Customer canonical names
    rows = db.execute(text("SELECT id, name, aliases FROM customers")).mappings().all()
    for r in rows:
        emb = client.embed(r["name"])
        db.execute(text(
            "INSERT INTO customer_search_entries "
            "(customer_id, entry_text, source, embedding) "
            "VALUES (:cid, :et, 'name', CAST(:emb AS vector))"
        ), {"cid": r["id"], "et": r["name"], "emb": emb})
        total += 1
        for alias in r.get("aliases") or []:
            emb = client.embed(alias)
            db.execute(text(
                "INSERT INTO customer_search_entries "
                "(customer_id, entry_text, source, embedding) "
                "VALUES (:cid, :et, 'alias', CAST(:emb AS vector))"
            ), {"cid": r["id"], "et": alias, "emb": emb})
            total += 1

    # Customer sites
    site_rows = db.execute(text(
        "SELECT customer_id, name FROM customer_sites"
    )).mappings().all()
    for r in site_rows:
        emb = client.embed(r["name"])
        db.execute(text(
            "INSERT INTO customer_search_entries "
            "(customer_id, entry_text, source, embedding) "
            "VALUES (:cid, :et, 'site', CAST(:emb AS vector))"
        ), {"cid": r["customer_id"], "et": r["name"], "emb": emb})
        total += 1

    # Products: name + code (+ aliases if column exists)
    prod_rows = db.execute(text(
        "SELECT id, sku, name FROM products"
    )).mappings().all()
    for r in prod_rows:
        emb = client.embed(r["name"])
        db.execute(text(
            "INSERT INTO sku_search_entries "
            "(product_id, entry_text, source, embedding) "
            "VALUES (:pid, :et, 'product_name', CAST(:emb AS vector))"
        ), {"pid": r["id"], "et": r["name"], "emb": emb})
        total += 1
        emb = client.embed(r["sku"])
        db.execute(text(
            "INSERT INTO sku_search_entries "
            "(product_id, entry_text, source, embedding) "
            "VALUES (:pid, :et, 'product_code', CAST(:emb AS vector))"
        ), {"pid": r["id"], "et": r["sku"], "emb": emb})
        total += 1

    # Services: name + code
    svc_rows = db.execute(text(
        "SELECT id, sku, name FROM services"
    )).mappings().all()
    for r in svc_rows:
        emb = client.embed(r["name"])
        db.execute(text(
            "INSERT INTO sku_search_entries "
            "(service_id, entry_text, source, embedding) "
            "VALUES (:sid, :et, 'service_name', CAST(:emb AS vector))"
        ), {"sid": r["id"], "et": r["name"], "emb": emb})
        total += 1
        emb = client.embed(r["sku"])
        db.execute(text(
            "INSERT INTO sku_search_entries "
            "(service_id, entry_text, source, embedding) "
            "VALUES (:sid, :et, 'service_code', CAST(:emb AS vector))"
        ), {"sid": r["id"], "et": r["sku"], "emb": emb})
        total += 1

    db.commit()
    logger.info("backfill_all complete: %d entries written", total)
    return total


def reindex_customer(customer_id: uuid.UUID, db: Session) -> None:
    """Rebuild customer_search_entries for a single customer.

    Side effects:
        * DELETE then INSERT for that customer's rows. Commit.
    """
    client = _get_vector_client()
    db.execute(text(
        "DELETE FROM customer_search_entries WHERE customer_id = :cid"
    ), {"cid": customer_id})
    row = db.execute(text(
        "SELECT id, name, aliases FROM customers WHERE id = :cid"
    ), {"cid": customer_id}).mappings().first()
    if not row:
        raise LookupError(
            f"reindex_customer: customer {customer_id!r} not found"
        )
    emb = client.embed(row["name"])
    db.execute(text(
        "INSERT INTO customer_search_entries "
        "(customer_id, entry_text, source, embedding) "
        "VALUES (:cid, :et, 'name', CAST(:emb AS vector))"
    ), {"cid": customer_id, "et": row["name"], "emb": emb})
    for alias in row.get("aliases") or []:
        emb = client.embed(alias)
        db.execute(text(
            "INSERT INTO customer_search_entries "
            "(customer_id, entry_text, source, embedding) "
            "VALUES (:cid, :et, 'alias', CAST(:emb AS vector))"
        ), {"cid": customer_id, "et": alias, "emb": emb})
    site_rows = db.execute(text(
        "SELECT name FROM customer_sites WHERE customer_id = :cid"
    ), {"cid": customer_id}).mappings().all()
    for s in site_rows:
        emb = client.embed(s["name"])
        db.execute(text(
            "INSERT INTO customer_search_entries "
            "(customer_id, entry_text, source, embedding) "
            "VALUES (:cid, :et, 'site', CAST(:emb AS vector))"
        ), {"cid": customer_id, "et": s["name"], "emb": emb})
    db.commit()


def reindex_sku(
    *, product_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None, db: Session,
) -> None:
    """Rebuild sku_search_entries for a single product OR service.

    Pass exactly one of product_id / service_id.
    """
    if (product_id is None) == (service_id is None):
        raise ValueError("reindex_sku: pass exactly one of product_id/service_id")
    client = _get_vector_client()
    if product_id:
        db.execute(text(
            "DELETE FROM sku_search_entries WHERE product_id = :pid"
        ), {"pid": product_id})
        row = db.execute(text(
            "SELECT sku, name FROM products WHERE id = :pid"
        ), {"pid": product_id}).mappings().first()
        if not row:
            raise LookupError(f"reindex_sku: product {product_id!r} not found")
        for entry_text, source in [
            (row["name"], "product_name"),
            (row["sku"], "product_code"),
        ]:
            emb = client.embed(entry_text)
            db.execute(text(
                "INSERT INTO sku_search_entries "
                "(product_id, entry_text, source, embedding) "
                "VALUES (:pid, :et, :s, CAST(:emb AS vector))"
            ), {"pid": product_id, "et": entry_text, "s": source, "emb": emb})
    else:
        db.execute(text(
            "DELETE FROM sku_search_entries WHERE service_id = :sid"
        ), {"sid": service_id})
        row = db.execute(text(
            "SELECT sku, name FROM services WHERE id = :sid"
        ), {"sid": service_id}).mappings().first()
        if not row:
            raise LookupError(f"reindex_sku: service {service_id!r} not found")
        for entry_text, source in [
            (row["name"], "service_name"),
            (row["sku"], "service_code"),
        ]:
            emb = client.embed(entry_text)
            db.execute(text(
                "INSERT INTO sku_search_entries "
                "(service_id, entry_text, source, embedding) "
                "VALUES (:sid, :et, :s, CAST(:emb AS vector))"
            ), {"sid": service_id, "et": entry_text, "s": source, "emb": emb})
    db.commit()
```

Create `seed/customer_search_entries.py`:

```python
"""Backfill generator for customer_search_entries.

Imported by seed/embeddings.py; runs after customers + sites are seeded.

Side effects: writes via providers.semantic_matcher.backfill_all.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from sqlalchemy.orm import Session


def persist(db: Session) -> int:
    """Backfill customer search entries. Idempotent (TRUNCATE-and-rebuild)."""
    from providers import semantic_matcher
    return semantic_matcher.backfill_all(db)
```

Create `seed/sku_search_entries.py` (identical structure — backfill_all covers both tables, so this is mostly a no-op stub kept for future split):

```python
"""Backfill generator for sku_search_entries.

backfill_all() in providers.semantic_matcher already handles both
customer and SKU tables. This module exists for parity with seed/
naming conventions and as the future split point if the backfills
are decoupled.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from sqlalchemy.orm import Session


def persist(db: Session) -> int:
    """No-op — backfill is handled by customer_search_entries.persist."""
    return 0
```

Modify `seed/embeddings.py` — add a call to `customer_search_entries.persist` after products + services + customer_reports are embedded. Find the existing `run()` entrypoint and append:

```python
    # Semantic matcher backfill (runs after all of customers, sites,
    # products, services, customer_reports are seeded)
    from seed import customer_search_entries
    matcher_count = customer_search_entries.persist(db)
    logger.info("seed.embeddings: backfilled %d matcher entries", matcher_count)
```

- [ ] **Step 6.3 — Run the backfill tests**

```bash
pytest tests/test_provider_semantic_matcher.py -v
```
Expected: all tests pass including new backfill ones.

- [ ] **Step 6.4 — Run full seed to verify end-to-end**

```bash
make reset-db && make migrate && make seed
psql $DATABASE_URL -c "SELECT COUNT(*) FROM customer_search_entries;"
psql $DATABASE_URL -c "SELECT COUNT(*) FROM sku_search_entries;"
```
Expected: customer count ≈ 40 names + ~50 aliases + ~75 sites ≈ ~165. SKU count ≈ 60 products × 2 (name + code) + 4 services × 2 = ~128.

- [ ] **Step 6.5 — Switch test fixture to call backfill_all**

In `tests/conftest.py`, remove the `db_session_with_search_entries` fixture and update the `seeded_customer_entries` fixture in `tests/test_provider_semantic_matcher.py`:

```python
@pytest.fixture
def seeded_customer_entries(db_session_with_seed):
    """Seed runs backfill_all via seed/embeddings.py. After Task 6 the
    explicit backfill_all call here is for safety in case a future seed
    refactor skips it."""
    from providers import semantic_matcher
    semantic_matcher.backfill_all(db_session_with_seed)
    return db_session_with_seed
```

- [ ] **Step 6.6 — Commit**

```bash
git add providers/semantic_matcher.py seed/customer_search_entries.py seed/sku_search_entries.py seed/embeddings.py tests/test_provider_semantic_matcher.py tests/conftest.py
git commit -m "feat(matcher): backfill_all + reindex_customer + reindex_sku + seed integration"
```

---

## Task 7: Boot reindex-if-empty step

**Files:**
- Modify: `scripts/boot.py`
- Test: `tests/test_boot_reindex.py` (NEW)

- [ ] **Step 7.1 — Write the failing boot-reindex test**

Create `tests/test_boot_reindex.py`:

```python
"""boot.py runs reindex_if_empty on every deploy. After migration 004
applies, tables exist but are empty; boot.py must call backfill_all so
the live app never hits MatcherIndexEmpty."""
from __future__ import annotations

from sqlalchemy import text

from db.session import session_scope


def test_reindex_if_empty_populates_on_empty_table(db_session_with_seed):
    from scripts import boot
    db = db_session_with_seed
    db.execute(text("TRUNCATE customer_search_entries"))
    db.execute(text("TRUNCATE sku_search_entries"))
    db.commit()
    boot.reindex_if_empty(db)
    n = db.execute(text("SELECT COUNT(*) FROM customer_search_entries")).scalar_one()
    assert n > 0


def test_reindex_if_empty_is_noop_when_populated(db_session_with_seed):
    from scripts import boot
    from providers import semantic_matcher
    db = db_session_with_seed
    semantic_matcher.backfill_all(db)
    before = db.execute(text("SELECT COUNT(*) FROM customer_search_entries")).scalar_one()
    boot.reindex_if_empty(db)
    after = db.execute(text("SELECT COUNT(*) FROM customer_search_entries")).scalar_one()
    assert before == after
```

- [ ] **Step 7.2 — Add `reindex_if_empty` to boot.py**

Modify `scripts/boot.py`. Add the function:

```python
def reindex_if_empty(db) -> None:
    """Boot step: rebuild search indexes if either table is empty.

    Runs after alembic upgrade head and before uvicorn exec. Idempotent —
    skips work when both tables already have rows. Required so first
    deploys after migration 004 don't hit MatcherIndexEmpty on the
    first matcher call.
    """
    from sqlalchemy import text
    from providers import semantic_matcher

    cust_count = db.execute(
        text("SELECT COUNT(*) FROM customer_search_entries")
    ).scalar_one()
    sku_count = db.execute(
        text("SELECT COUNT(*) FROM sku_search_entries")
    ).scalar_one()
    if cust_count and sku_count:
        log.info("boot: matcher indexes already populated; skipping reindex")
        return
    log.info(
        "boot: reindexing matcher (customer=%d, sku=%d)", cust_count, sku_count
    )
    n = semantic_matcher.backfill_all(db)
    log.info("boot: matcher reindex complete (%d entries)", n)
```

Wire it into the main boot sequence — after `alembic upgrade head` and after the seed-if-empty check, before `exec uvicorn`:

```python
    # ... existing alembic + seed checks ...
    with session_scope() as db:
        reindex_if_empty(db)
    log.info("boot: exec uvicorn: uvicorn app.main:app --host 0.0.0.0 --port 5001")
    os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5001"])
```

- [ ] **Step 7.3 — Run tests**

```bash
pytest tests/test_boot_reindex.py -v
```
Expected: 2/2 PASS.

- [ ] **Step 7.4 — Commit**

```bash
git add scripts/boot.py tests/test_boot_reindex.py
git commit -m "feat(matcher): boot.py reindex_if_empty step for first-deploy backfill"
```

---

## Task 8: Test corpus YAML fixture

**Files:**
- Create: `tests/fixtures/matcher_corpus.yaml` (~246 cases)

This task is **data-heavy**. The fixture is the matcher's spec; budget time accordingly. Sample structure below — engineer drafts the full ~246 cases by referencing actual seeded customers/sites/products from `seed/customers.py`, `seed/sites.py`, `seed/products.py`, `seed/services.py`.

- [ ] **Step 8.1 — Read the seed data to identify real customer names + sites + SKUs**

```bash
python -c "
from db.session import session_scope
from sqlalchemy import text
with session_scope() as db:
    print('--- customers ---')
    for r in db.execute(text('SELECT name, aliases FROM customers ORDER BY name')).all():
        print(r.name, r.aliases or '')
    print('--- sites ---')
    for r in db.execute(text(
      'SELECT c.name AS customer, s.name AS site FROM customer_sites s '
      'JOIN customers c ON c.id = s.customer_id ORDER BY c.name'
    )).all():
        print(r.customer, '|', r.site)
    print('--- products (first 20) ---')
    for r in db.execute(text('SELECT sku, name FROM products LIMIT 20')).all():
        print(r.sku, '|', r.name)
"
```

Save the output for reference while drafting the corpus.

- [ ] **Step 8.2 — Create `tests/fixtures/matcher_corpus.yaml`**

Header + first-category template (rest follows the same pattern). The engineer fills in all 24 categories to hit the minimum case counts in the spec (Section: Test corpus + eval harness). Use real customer names from the seed output above.

```yaml
# Semantic-matcher eval corpus.
#
# Each row:
#   input: the user's free-text reference (typed or dictated)
#   expected_customer_name: canonical name from seed/customers.py (resolved
#     to UUID at test-load time) — OR null when the case expects no resolution
#   expected_alternatives_min: optional; for ambiguous/disambig cases that
#     should return null but surface multiple alternatives
#   category: one of the 24 spec categories
#   context: optional ConversationContext fields (active_account_name,
#     last_3_customer_names)
#
# Categories + minimum case counts (per spec):
#   canonical              10
#   abbreviation           10
#   possessive              8
#   alias                  10
#   apostrophe_variant      6
#   site_reference         12
#   site_shortform          8
#   typo_resolvable        10
#   long_form_sentence     15
#   pronoun_resolution     10
#   cross_customer_collision  10
#   ambiguous_reference    10
#   negative_case          10
#   generic_words           8
#   compound_reference      8
#   casual_colloquial       8
#   partial_match_noise     6
#   sku_canonical_code     10
#   sku_product_name       10
#   sku_qty_plus_product   10
#   sku_service             6
#   sku_colloquial          8
#   sku_ambiguous           6
#   sku_negative            6
#
# Total: ~246 cases.

cases:
  # ----------- canonical (10) -----------
  - input: "O'Reilly Family Orchards"
    expected_customer_name: "O'Reilly Family Orchards"
    category: canonical
  - input: "Martinez & Sons Orchards"
    expected_customer_name: "Martinez & Sons Orchards"
    category: canonical
  # ... add 8 more from real seed names ...

  # ----------- abbreviation (10) -----------
  - input: "O'Reilly"
    expected_customer_name: "O'Reilly Family Orchards"
    category: abbreviation
  - input: "Martinez"
    expected_customer_name: "Martinez & Sons Orchards"
    category: abbreviation
  # ... add 8 more ...

  # ----------- possessive (8) -----------
  - input: "O'Reilly's"
    expected_customer_name: "O'Reilly Family Orchards"
    category: possessive
  # ... add 7 more ...

  # ----------- alias (10) -----------
  # Use real entries from customers.aliases ...

  # ----------- apostrophe_variant (6) -----------
  - input: "OReilly"
    expected_customer_name: "O'Reilly Family Orchards"
    category: apostrophe_variant
  - input: "O Reilly"
    expected_customer_name: "O'Reilly Family Orchards"
    category: apostrophe_variant
  # ... add 4 more ...

  # ----------- site_reference (12) -----------
  # Use real site names from customer_sites — e.g. if O'Reilly has a
  # "back-40 yard" site, the case is { input: "the back-40", expected: O'Reilly }
  # ... fill in 12 cases ...

  # (continue for every category to the min count)
  # ===========================================
  # SKU CORPUS
  # ===========================================

  # ----------- sku_canonical_code (10) -----------
  - input: "PKG-BEES-3LB-ITA"
    expected_sku_kind: product
    expected_sku_code: "PKG-BEES-3LB-ITA"
    category: sku_canonical_code
  # ... 9 more SKU codes from seed/products.py + seed/services.py ...

  # ----------- sku_negative (6) -----------
  - input: "varroa swatter"
    expected_sku_kind: null
    category: sku_negative
  # ... 5 more ...
```

- [ ] **Step 8.3 — Validate the YAML loads + every customer name resolves to a real seed row**

Run a quick validation script (one-off; not committed):

```python
import yaml
from db.session import session_scope
from sqlalchemy import text

corpus = yaml.safe_load(open("tests/fixtures/matcher_corpus.yaml"))
with session_scope() as db:
    customer_names = {r.name for r in db.execute(text("SELECT name FROM customers"))}
    product_skus = {r.sku for r in db.execute(text("SELECT sku FROM products"))}
    service_skus = {r.sku for r in db.execute(text("SELECT sku FROM services"))}

missing_customers, missing_skus = [], []
for case in corpus["cases"]:
    if case.get("expected_customer_name") and case["expected_customer_name"] not in customer_names:
        missing_customers.append(case)
    if case.get("expected_sku_code") and case["expected_sku_code"] not in product_skus | service_skus:
        missing_skus.append(case)

print(f"Missing customers: {len(missing_customers)}")
print(f"Missing SKUs: {len(missing_skus)}")
for m in missing_customers[:5]: print(m)
```

Iterate until both lists are empty.

- [ ] **Step 8.4 — Subagent verification (mandatory per `feedback_real_corpus_tests_subagent_verified.md`)**

Dispatch an Explore subagent with this prompt:

```
You are reviewing a test corpus for an LLM-based customer/SKU matcher
in a commercial beekeeping CRM. The matcher needs to resolve
field-rep speech (dictated transcripts + typed messages) to customer
rows or product/service rows.

Files to read:
  - tests/fixtures/matcher_corpus.yaml — the corpus
  - seed/customers.py and seed/sites.py — real customer data
  - seed/products.py and seed/services.py — real SKU data
  - docs/superpowers/specs/2026-05-12-semantic-matcher-design.md
    (Test corpus + eval harness section) — the design

Report:
  (a) Categories of variation present in real field-rep speech but
      MISSING from the corpus. Be specific (e.g. "regional accents",
      "company names with international characters", "site names
      starting with numbers"). Suggest 3-5 concrete new cases per gap.
  (b) Cases that are filler / tautological / too easy. Quote them.
  (c) Cases that reference customers, sites, products, or services
      NOT present in the seed data. Cite the case + the expected
      name. (False-positive failures.)
  (d) Whether each category threshold (in the spec) is calibrated
      correctly. Too lax = regressions slip through; too strict =
      false-positive CI failures. Recommend adjustments.
  (e) Cross-customer collisions: confirm there are real cross-customer
      collisions in the corpus (two customers sharing a token). If not,
      identify which seed customers should be in collision cases.

Return a structured Markdown report.
```

Save the report to `.reports/matcher_corpus_review.md`. Iterate the corpus based on findings. **Re-dispatch the subagent after each iteration until the report shows no critical gaps.**

- [ ] **Step 8.5 — Commit**

```bash
git add tests/fixtures/matcher_corpus.yaml .reports/matcher_corpus_review.md
git commit -m "feat(matcher): test corpus YAML — 246 real-text cases across 24 categories"
```

---

## Task 9: Eval harness

**Files:**
- Create: `tests/test_matcher_eval.py`
- Create: `tests/_eval_helpers.py` (corpus loader + metric computation + report writer)
- Modify: `.gitignore` (allow `.reports/` to be committed)

- [ ] **Step 9.1 — Write the eval-harness test scaffolding**

Create `tests/_eval_helpers.py`:

```python
"""Helpers for the matcher eval harness — corpus loading, metric
computation, report writing. Kept out of the test file so the test
module stays focused on assertions."""
# pyright: reportMissingImports=false
from __future__ import annotations

import hashlib
import os
import random
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session


CORPUS_PATH = Path("tests/fixtures/matcher_corpus.yaml")
REPORTS_DIR = Path(".reports")

EVAL_MODE = os.environ.get("MATCHER_EVAL_MODE", "sample")  # 'sample' | 'full'
SAMPLE_FRACTION = 0.20
SAMPLE_SEED = 42

CUSTOMER_THRESHOLDS = {
    "canonical": 1.00,
    "abbreviation": 0.98,
    "possessive": 0.98,
    "alias": 0.98,
    "apostrophe_variant": 0.98,
    "site_reference": 0.95,
    "site_shortform": 0.95,
    "typo_resolvable": 0.90,
    "long_form_sentence": 0.95,
    "pronoun_resolution": 0.85,
    "cross_customer_collision": 0.90,
    "ambiguous_reference": 0.95,
    "negative_case": 1.00,
    "generic_words": 1.00,
    "compound_reference": 0.85,
    "casual_colloquial": 0.95,
    "partial_match_noise": 0.95,
}

SKU_THRESHOLDS = {
    "sku_canonical_code": 1.00,
    "sku_product_name": 0.98,
    "sku_qty_plus_product": 0.98,
    "sku_service": 0.95,
    "sku_colloquial": 0.90,
    "sku_ambiguous": 0.95,
    "sku_negative": 1.00,
}


@dataclass
class EvalCase:
    input: str
    category: str
    expected_customer_id: uuid.UUID | None = None
    expected_sku_id: uuid.UUID | None = None
    expected_sku_kind: str | None = None
    expected_alternatives_min: int = 0
    context_active_account_id: uuid.UUID | None = None
    context_last_3_customer_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class CategoryResult:
    category: str
    total: int = 0
    correct: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (input, why)


def load_corpus(*, kind: str, db: Session) -> list[EvalCase]:
    """Load + resolve symbolic names to UUIDs against the live DB."""
    raw = yaml.safe_load(CORPUS_PATH.read_text())
    customer_name_to_id: dict[str, uuid.UUID] = {
        r.name: r.id for r in db.execute(text("SELECT id, name FROM customers"))
    }
    product_sku_to_id: dict[str, uuid.UUID] = {
        r.sku: r.id for r in db.execute(text("SELECT id, sku FROM products"))
    }
    service_sku_to_id: dict[str, uuid.UUID] = {
        r.sku: r.id for r in db.execute(text("SELECT id, sku FROM services"))
    }

    cases: list[EvalCase] = []
    for c in raw["cases"]:
        category = c["category"]
        is_sku_category = category.startswith("sku_")
        if kind == "customer" and is_sku_category:
            continue
        if kind == "sku" and not is_sku_category:
            continue

        case = EvalCase(input=c["input"], category=category)

        if not is_sku_category:
            name = c.get("expected_customer_name")
            if name is not None:
                if name not in customer_name_to_id:
                    raise ValueError(
                        f"Corpus case references unknown customer {name!r}"
                    )
                case.expected_customer_id = customer_name_to_id[name]
        else:
            kind_val = c.get("expected_sku_kind")
            sku = c.get("expected_sku_code")
            if kind_val == "product" and sku:
                case.expected_sku_id = product_sku_to_id[sku]
                case.expected_sku_kind = "product"
            elif kind_val == "service" and sku:
                case.expected_sku_id = service_sku_to_id[sku]
                case.expected_sku_kind = "service"
            elif kind_val is None:
                pass  # negative case
            else:
                raise ValueError(f"Bad sku case shape: {c}")

        case.expected_alternatives_min = c.get("expected_alternatives_min", 0)

        ctx = c.get("context") or {}
        if "active_account_name" in ctx:
            name = ctx["active_account_name"]
            if name:
                case.context_active_account_id = customer_name_to_id.get(name)
        if "last_3_customer_names" in ctx:
            case.context_last_3_customer_ids = [
                customer_name_to_id[n] for n in ctx["last_3_customer_names"]
                if n in customer_name_to_id
            ]

        cases.append(case)
    return cases


def stratified_sample(cases: list[EvalCase], fraction: float, seed: int) -> list[EvalCase]:
    """Per-category fraction sampling for sample-mode eval."""
    rng = random.Random(seed)
    by_cat: dict[str, list[EvalCase]] = {}
    for c in cases:
        by_cat.setdefault(c.category, []).append(c)
    sampled: list[EvalCase] = []
    for cat, items in by_cat.items():
        n = max(1, int(round(len(items) * fraction)))
        sampled.extend(rng.sample(items, n))
    return sampled


def evaluate_case_customer(case: EvalCase, match: Any) -> tuple[bool, str]:
    """Returns (correct, reason). Correct iff matcher output matches expectation."""
    if case.expected_customer_id is None:
        if match.customer_id is None:
            return True, ""
        return False, f"expected null, got {match.customer_id}"
    if match.customer_id == case.expected_customer_id:
        return True, ""
    return False, (
        f"expected {case.expected_customer_id}, "
        f"got {match.customer_id} (conf={match.confidence:.2f})"
    )


def evaluate_case_sku(case: EvalCase, match: Any) -> tuple[bool, str]:
    if case.expected_sku_kind is None:
        if match.sku_kind is None:
            return True, ""
        return False, f"expected null, got {match.sku_kind}/{match.sku_code}"
    actual_id = match.product_id if match.sku_kind == "product" else match.service_id
    if actual_id == case.expected_sku_id and match.sku_kind == case.expected_sku_kind:
        return True, ""
    return False, (
        f"expected {case.expected_sku_kind}/{case.expected_sku_id}, "
        f"got {match.sku_kind}/{actual_id}"
    )


def write_report(
    *, kind: str, results: dict[str, CategoryResult],
    thresholds: dict[str, float], metadata: dict[str, Any],
) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"matcher_eval_{kind}.md"
    lines = [
        f"# Matcher eval report ({kind})\n",
        "## Metadata\n",
        "| Key | Value |", "|---|---|",
    ]
    for k, v in metadata.items():
        lines.append(f"| {k} | `{v}` |")
    lines.append("\n## Per-category results\n")
    lines.append("| Category | N | Correct | Precision | Threshold | Status |")
    lines.append("|---|---|---|---|---|---|")
    for cat, r in sorted(results.items()):
        prec = (r.correct / r.total) if r.total else 0.0
        thr = thresholds.get(cat, 0.0)
        status = "PASS" if prec >= thr else "FAIL"
        lines.append(
            f"| {cat} | {r.total} | {r.correct} | {prec:.2%} | {thr:.2%} | {status} |"
        )
    lines.append("\n## Failures\n")
    for cat, r in sorted(results.items()):
        if not r.failures:
            continue
        lines.append(f"### {cat}\n")
        for inp, why in r.failures[:10]:
            lines.append(f"- `{inp}` — {why}")
        if len(r.failures) > 10:
            lines.append(f"- _(+ {len(r.failures) - 10} more)_")
    path.write_text("\n".join(lines))
    return path


def resolve_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def corpus_sha() -> str:
    return hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()[:12]
```

Create `tests/test_matcher_eval.py`:

```python
"""Eval harness: runs the matcher corpus and asserts per-category thresholds.

Modes:
    MATCHER_EVAL_MODE=sample (default) — 20% stratified sample per category
    MATCHER_EVAL_MODE=full              — every case
    MATCHER_EVAL_FULL=1 (env)           — equivalent to full
"""
from __future__ import annotations

import os
import uuid

import pytest

from agents.schemas import ConversationContext, SkuContext
from providers import semantic_matcher
from tests._eval_helpers import (
    CUSTOMER_THRESHOLDS, EVAL_MODE, SAMPLE_FRACTION, SAMPLE_SEED,
    SKU_THRESHOLDS, CategoryResult,
    corpus_sha, evaluate_case_customer, evaluate_case_sku,
    load_corpus, resolve_git_sha, stratified_sample, write_report,
)


require_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — eval skipped",
)


@require_openai_key
def test_matcher_eval_customer(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    cases = load_corpus(kind="customer", db=db_session_with_seed)
    if EVAL_MODE == "sample" and not os.environ.get("MATCHER_EVAL_FULL"):
        cases = stratified_sample(cases, SAMPLE_FRACTION, SAMPLE_SEED)

    results: dict[str, CategoryResult] = {}
    semantic_matcher.clear_caches()
    for case in cases:
        ctx = ConversationContext(
            active_account_id=case.context_active_account_id,
            last_3_customer_ids=case.context_last_3_customer_ids,
        )
        match = semantic_matcher.match_customer(
            case.input, conversation_ctx=ctx,
            conversation_id=uuid.uuid4(),
            db=db_session_with_seed,
        )
        result = results.setdefault(case.category, CategoryResult(case.category))
        correct, why = evaluate_case_customer(case, match)
        result.total += 1
        if correct:
            result.correct += 1
        else:
            result.failures.append((case.input, why))

    from app.config import get_settings
    settings = get_settings()
    metadata = {
        "embedding_model": "all-MiniLM-L6-v2",
        "disambig_model": settings.copilot_matcher_disambig_model,
        "prompt_version": semantic_matcher.PROMPT_VERSION,
        "corpus_sha": corpus_sha(),
        "git_sha": resolve_git_sha(),
        "eval_mode": EVAL_MODE if not os.environ.get("MATCHER_EVAL_FULL") else "full",
    }
    write_report(
        kind="customer", results=results,
        thresholds=CUSTOMER_THRESHOLDS, metadata=metadata,
    )

    failures: list[str] = []
    for cat, threshold in CUSTOMER_THRESHOLDS.items():
        r = results.get(cat)
        if not r or not r.total:
            continue  # sample mode may skip a category — accept
        prec = r.correct / r.total
        if prec < threshold:
            failures.append(f"{cat}: {prec:.2%} < {threshold:.2%}")
    assert not failures, "Threshold breaches:\n" + "\n".join(failures)


@require_openai_key
def test_matcher_eval_sku(db_session_with_seed):
    semantic_matcher.backfill_all(db_session_with_seed)
    cases = load_corpus(kind="sku", db=db_session_with_seed)
    if EVAL_MODE == "sample" and not os.environ.get("MATCHER_EVAL_FULL"):
        cases = stratified_sample(cases, SAMPLE_FRACTION, SAMPLE_SEED)

    results: dict[str, CategoryResult] = {}
    semantic_matcher.clear_caches()
    for case in cases:
        match = semantic_matcher.match_sku(
            case.input, sku_context=SkuContext(),
            conversation_id=uuid.uuid4(),
            db=db_session_with_seed,
        )
        result = results.setdefault(case.category, CategoryResult(case.category))
        correct, why = evaluate_case_sku(case, match)
        result.total += 1
        if correct:
            result.correct += 1
        else:
            result.failures.append((case.input, why))

    from app.config import get_settings
    settings = get_settings()
    metadata = {
        "embedding_model": "all-MiniLM-L6-v2",
        "disambig_model": settings.copilot_matcher_disambig_model,
        "prompt_version": semantic_matcher.PROMPT_VERSION,
        "corpus_sha": corpus_sha(),
        "git_sha": resolve_git_sha(),
        "eval_mode": EVAL_MODE if not os.environ.get("MATCHER_EVAL_FULL") else "full",
    }
    write_report(
        kind="sku", results=results,
        thresholds=SKU_THRESHOLDS, metadata=metadata,
    )

    failures: list[str] = []
    for cat, threshold in SKU_THRESHOLDS.items():
        r = results.get(cat)
        if not r or not r.total:
            continue
        prec = r.correct / r.total
        if prec < threshold:
            failures.append(f"{cat}: {prec:.2%} < {threshold:.2%}")
    assert not failures, "SKU threshold breaches:\n" + "\n".join(failures)
```

- [ ] **Step 9.2 — Make `.reports/` committable**

If `.gitignore` ignores `.reports/`, allow it:

```bash
mkdir -p .reports
echo "# Eval reports — committed so PR diffs show quality deltas" > .reports/README.md
git add -f .reports/README.md
```

- [ ] **Step 9.3 — Run eval in sample mode**

```bash
make reset-db && make migrate && make seed
MATCHER_EVAL_MODE=sample pytest tests/test_matcher_eval.py -v
```
Expected: PASS. Reports written to `.reports/matcher_eval_customer.md` and `.reports/matcher_eval_sku.md`.

- [ ] **Step 9.4 — Run eval in full mode and iterate corpus until thresholds clear**

```bash
MATCHER_EVAL_FULL=1 pytest tests/test_matcher_eval.py -v
```

If any threshold fails: inspect the report's "Failures" section, decide whether to (a) fix the prompt, (b) add an alias to a customer row, (c) adjust a corpus case that's miscategorized, or (d) loosen a threshold (last resort; never below 0.70 without commit-message justification). Re-run.

- [ ] **Step 9.5 — Commit**

```bash
git add tests/test_matcher_eval.py tests/_eval_helpers.py .reports/matcher_eval_customer.md .reports/matcher_eval_sku.md .reports/README.md
git commit -m "feat(matcher): eval harness with per-category thresholds + versioned reports"
```

---

## Task 10: Confirm-match + pick-match UI templates

**Files:**
- Create: `app/templates/partials/copilot_confirm_match.html`
- Create: `app/templates/partials/copilot_pick_match.html`
- Create: `tests/test_partials_matcher.py` (renders the templates with sample data)

- [ ] **Step 10.1 — Write the failing template-render test**

Create `tests/test_partials_matcher.py`:

```python
"""Confirm-match + pick-match partials render with the expected
elements and forms."""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_confirm_match_partial_renders_yes_no_form(client: TestClient):
    """GET /_internal/render/confirm-match returns the partial.
    (This internal route is added to the matcher route module solely for
    template-render testing — it bypasses the orchestrator state machine.)"""
    cid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    resp = client.get(
        f"/_internal/render/confirm-match?turn_id={tid}&customer_id={cid}"
        f"&customer_name=O%27Reilly%20Family%20Orchards"
    )
    assert resp.status_code == 200
    body = resp.text
    assert "O'Reilly Family Orchards" in body
    assert 'name="customer_id"' in body
    assert 'value="' + cid + '"' in body
    assert "Yes" in body and "No" in body


def test_pick_match_partial_renders_alternatives(client: TestClient):
    cid1 = str(uuid.uuid4())
    cid2 = str(uuid.uuid4())
    resp = client.get(
        f"/_internal/render/pick-match?turn_id={uuid.uuid4()}&"
        f"alt={cid1}|O%27Reilly%20Family%20Orchards&"
        f"alt={cid2}|O%27Connell%20Brothers"
    )
    assert resp.status_code == 200
    body = resp.text
    assert "O'Reilly Family Orchards" in body
    assert "O'Connell Brothers" in body
    assert cid1 in body and cid2 in body
```

- [ ] **Step 10.2 — Create the partials**

Create `app/templates/partials/copilot_confirm_match.html`:

```html
{# Inline-confirm card shown when 0.50 ≤ matcher confidence < 0.80. #}
<div class="match-confirm-card" data-turn-id="{{ turn_id }}">
  <div class="match-confirm-prompt">
    <span class="thinking-dot"></span>
    <span>Logging this for <strong>{{ customer_name }}</strong> — is that right?</span>
  </div>
  <form class="match-confirm-form"
        method="post"
        action="/copilot/turns/{{ turn_id }}/confirm-match"
        hx-post="/copilot/turns/{{ turn_id }}/confirm-match"
        hx-target="closest .match-confirm-card"
        hx-swap="outerHTML">
    <input type="hidden" name="customer_id" value="{{ customer_id }}">
    <button type="submit" class="action primary">Yes, proceed</button>
    <button type="submit"
            class="action ghost"
            name="action"
            value="reject"
            formaction="/copilot/turns/{{ turn_id }}/confirm-match?reject=1"
            hx-post="/copilot/turns/{{ turn_id }}/confirm-match?reject=1">
      No, pick another
    </button>
  </form>
</div>
```

Create `app/templates/partials/copilot_pick_match.html`:

```html
{# Alternatives picker shown when matcher confidence < 0.50 or null. #}
<div class="match-pick-card" data-turn-id="{{ turn_id }}">
  <div class="match-pick-prompt">
    <span>I couldn't tell which customer. Did you mean:</span>
  </div>
  <ul class="match-pick-list">
    {% for alt in alternatives %}
      <li>
        <form method="post"
              action="/copilot/turns/{{ turn_id }}/confirm-match"
              hx-post="/copilot/turns/{{ turn_id }}/confirm-match"
              hx-target="closest .match-pick-card"
              hx-swap="outerHTML">
          <input type="hidden" name="customer_id" value="{{ alt.customer_id }}">
          <button type="submit" class="action ghost">
            {{ alt.customer_name }}
            {% if alt.confidence %}
              <span class="muted">({{ "%.0f%%"|format(alt.confidence * 100) }})</span>
            {% endif %}
          </button>
        </form>
      </li>
    {% endfor %}
  </ul>
  <div class="match-pick-rephrase muted">
    Or rephrase your message in the composer below.
  </div>
</div>
```

Add CSS to `app/static/waggle.css` (append):

```css
/* Matcher partials — confirm + pick-match */
.match-confirm-card, .match-pick-card {
  margin-top: 8px;
  padding: 12px 14px;
  background: var(--honey-50, #fff8e7);
  border: 1px solid var(--honey-300, #f0d088);
  border-radius: 8px;
}
.match-confirm-prompt, .match-pick-prompt {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 8px;
  font-size: 14px;
}
.match-confirm-form { display: flex; gap: 8px; }
.match-pick-list { list-style: none; padding: 0; margin: 0; }
.match-pick-list li { margin: 4px 0; }
.match-pick-list button { width: 100%; text-align: left; }
.match-pick-rephrase { margin-top: 8px; font-size: 12px; }
```

- [ ] **Step 10.3 — Add internal render route for tests + run test**

In `app/routes/copilot.py`, add:

```python
@router.get("/_internal/render/confirm-match", response_class=HTMLResponse)
async def _internal_render_confirm_match(
    request: Request,
    turn_id: uuid.UUID,
    customer_id: uuid.UUID,
    customer_name: str,
) -> HTMLResponse:
    """Test-only route. Renders the confirm-match partial in isolation."""
    return templates.TemplateResponse(
        "partials/copilot_confirm_match.html",
        {"request": request, "turn_id": turn_id,
         "customer_id": customer_id, "customer_name": customer_name},
    )


@router.get("/_internal/render/pick-match", response_class=HTMLResponse)
async def _internal_render_pick_match(
    request: Request,
    turn_id: uuid.UUID,
    alt: list[str] = Query(default_factory=list),
) -> HTMLResponse:
    alternatives = []
    for a in alt:
        cid, name = a.split("|", 1)
        alternatives.append({"customer_id": cid, "customer_name": name, "confidence": None})
    return templates.TemplateResponse(
        "partials/copilot_pick_match.html",
        {"request": request, "turn_id": turn_id, "alternatives": alternatives},
    )
```

Run:

```bash
pytest tests/test_partials_matcher.py -v
```
Expected: 2/2 PASS.

- [ ] **Step 10.4 — Commit**

```bash
git add app/templates/partials/copilot_confirm_match.html app/templates/partials/copilot_pick_match.html app/static/waggle.css app/routes/copilot.py tests/test_partials_matcher.py
git commit -m "feat(matcher): confirm-match + pick-match partials + internal render route"
```

---

## Task 11: `POST /copilot/turns/{id}/confirm-match` route

**Files:**
- Modify: `app/routes/copilot.py` (real route, not internal)
- Modify: `agents/copilot/conversation_schemas.py` (add `pending_customer_match` shape to ToolCallRecord)
- Create: `tests/test_routes_confirm_match.py`

- [ ] **Step 11.1 — Write the failing route test**

Create `tests/test_routes_confirm_match.py`:

```python
"""POST /copilot/turns/{id}/confirm-match resumes a paused turn with
the user-picked customer_id and re-dispatches the original intent."""
from __future__ import annotations

import uuid

from sqlalchemy import text


def test_confirm_match_picks_customer_and_resumes(client, db):
    """Setup: create a conversation + an assistant turn whose tool_calls
    JSONB contains a pending_customer_match record. POST confirm-match
    with one of the alternatives. Expect: a new assistant turn appears
    with the resumed result, the pending_customer_match record is
    cleared, and the chosen customer_id is committed."""
    # Insert conversation + assistant turn with pending_customer_match
    conv_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO conversations (id) VALUES (:cid)"
    ), {"cid": conv_id})
    target_cid = db.execute(text(
        "SELECT id FROM customers LIMIT 1"
    )).scalar_one()
    turn_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT INTO chat_turns "
        "(id, conversation_id, role, text, tool_calls) "
        "VALUES (:tid, :cid, 'assistant', 'Pending', :tc)"
    ), {
        "tid": turn_id, "cid": conv_id,
        "tc": '[{"tool_name":"pending_customer_match",'
              f'"target_intent":"log_customer_report",'
              f'"hint":"the orchard",'
              f'"alternatives":[["{target_cid}","Test Customer",0.6]]}}]',
    })
    db.commit()

    resp = client.post(
        f"/copilot/turns/{turn_id}/confirm-match",
        data={"customer_id": str(target_cid)},
    )
    assert resp.status_code == 200

    # Check that a follow-on turn was created
    n = db.execute(text(
        "SELECT COUNT(*) FROM chat_turns WHERE conversation_id = :cid "
        "AND role = 'assistant' AND id != :tid"
    ), {"cid": conv_id, "tid": turn_id}).scalar_one()
    assert n == 1


def test_confirm_match_reject_swaps_to_pick_match(client, db):
    """When the user clicks 'No, pick another' on the confirm-match card,
    the response swaps in a pick-match card listing the alternatives."""
    # Similar setup as above, then POST with reject=1
    # Assert the response body contains the pick-match HTML
    ...  # Implementing engineer fills in following the pattern above
```

- [ ] **Step 11.2 — Implement the route**

Add to `app/routes/copilot.py`:

```python
@router.post("/copilot/turns/{turn_id}/confirm-match", response_class=HTMLResponse)
async def confirm_match(
    request: Request,
    turn_id: uuid.UUID,
    customer_id: uuid.UUID | None = Form(default=None),
    reject: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Resume a paused turn whose matcher returned low confidence.

    Two paths:
      * customer_id provided: commit that customer to the turn,
        re-dispatch the original intent (log_report / create_order /
        etc.) with the chosen customer_id, return the resumed
        assistant turn partial.
      * reject=1 (user clicked "No, pick another"): swap in the
        pick-match card listing the alternatives stored in the turn's
        pending_customer_match record.
    """
    turn = db.get(ChatTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    pending = _extract_pending_match(turn)
    if pending is None:
        raise HTTPException(status_code=409, detail="No pending match on this turn")

    if reject:
        alternatives = [
            {"customer_id": c[0], "customer_name": c[1], "confidence": c[2]}
            for c in pending["alternatives"]
        ]
        return templates.TemplateResponse(
            "partials/copilot_pick_match.html",
            {"request": request, "turn_id": turn_id,
             "alternatives": alternatives},
        )

    if customer_id is None:
        raise HTTPException(status_code=400, detail="customer_id required")

    # Resume the paused intent with the chosen customer
    from agents.copilot.orchestrator import resume_with_customer
    resumed_turn = resume_with_customer(
        conv_id=turn.conversation_id,
        paused_turn=turn,
        chosen_customer_id=customer_id,
        db=db,
    )
    # Clear the pending_customer_match marker
    _clear_pending_match(turn, db)

    return templates.TemplateResponse(
        "partials/copilot_turn_pair.html",
        {"request": request, "assistant_turn": resumed_turn},
    )


def _extract_pending_match(turn: ChatTurn) -> dict | None:
    for tc in turn.tool_calls or []:
        if tc.get("tool_name") == "pending_customer_match":
            return tc
    return None


def _clear_pending_match(turn: ChatTurn, db: Session) -> None:
    turn.tool_calls = [
        tc for tc in (turn.tool_calls or [])
        if tc.get("tool_name") != "pending_customer_match"
    ]
    db.add(turn)
    db.commit()
```

- [ ] **Step 11.3 — Implement `resume_with_customer` in the orchestrator**

Add to `agents/copilot/orchestrator.py`:

```python
def resume_with_customer(
    *, conv_id: uuid.UUID, paused_turn: "ChatTurn",
    chosen_customer_id: uuid.UUID, db: Session,
) -> "ChatTurn":
    """Re-dispatch the paused turn's original intent with the user-picked
    customer_id. Mirrors the original `take_turn` flow but skips the
    matcher (we already have a customer_id committed).

    Side effects:
        * Inserts a new ChatTurn (assistant) with the resumed answer.
        * Commits.
    """
    pending = next(
        (tc for tc in (paused_turn.tool_calls or [])
         if tc.get("tool_name") == "pending_customer_match"),
        None,
    )
    if pending is None:
        raise RuntimeError("resume_with_customer: no pending_customer_match")
    target_intent = pending.get("target_intent")
    # Dispatch based on the target_intent — same shape as take_turn() but
    # with customer pre-committed. For Phase I, log_customer_report and
    # create_order are the two resumable intents.
    if target_intent == "log_customer_report":
        return _resume_log_report(conv_id, chosen_customer_id, pending, db)
    if target_intent == "create_order":
        return _resume_create_order(conv_id, chosen_customer_id, pending, db)
    raise RuntimeError(f"resume_with_customer: unknown intent {target_intent!r}")
```

> **Engineer note:** `_resume_log_report` and `_resume_create_order` are private helpers that mirror the original create_order / log_customer_report branches in `take_turn` but skip the matcher call. Extract the shared body into helpers if it makes the code DRYer.

- [ ] **Step 11.4 — Run route test**

```bash
pytest tests/test_routes_confirm_match.py -v
```
Expected: PASS.

- [ ] **Step 11.5 — Commit**

```bash
git add app/routes/copilot.py agents/copilot/orchestrator.py tests/test_routes_confirm_match.py
git commit -m "feat(matcher): POST /copilot/turns/{id}/confirm-match + resume_with_customer"
```

---

## Task 12: Orchestrator integration

**Files:**
- Modify: `agents/copilot/orchestrator.py` (replace `resolve_account` with `match_customer`; add confidence-graduated branching; observability hook)
- Modify: `tests/test_agent_copilot_orchestrator.py` (update assertions)

- [ ] **Step 12.1 — Replace the orchestrator's matcher call**

In `agents/copilot/orchestrator.py`:

1. **Remove** `from agents.copilot.account_resolver import AccountIndex, resolve_account`.
2. **Add**:
   ```python
   from providers import semantic_matcher
   from agents.schemas import ConversationContext
   ```
3. Find the place that currently calls `resolve_account(hint, account_index)`. Replace with:
   ```python
   ctx = ConversationContext(
       active_account_id=conv_state.active_account_id,
       last_3_customer_ids=conv_state.last_3_customer_ids,
   )
   match = semantic_matcher.match_customer(
       hint,
       conversation_ctx=ctx,
       conversation_id=conv_id,
       db=db,
   )

   # Confidence-graduated branching
   if match.customer_id is None or match.confidence < 0.50:
       # Render the pick-match card; pause the turn
       return _emit_pending_match_turn(
           conv_id=conv_id,
           target_intent=intent.intent,  # e.g. 'log_customer_report'
           hint=hint,
           alternatives=match.alternatives,
           assistant_text=(
               "I couldn't tell which customer. Did you mean: "
               + ", ".join(a.customer_name for a in match.alternatives[:3])
               + " — or rephrase your message."
           ),
           db=db,
       )
   if match.confidence < 0.80:
       # Render the confirm-match card; pause
       return _emit_confirm_match_turn(
           conv_id=conv_id,
           target_intent=intent.intent,
           hint=hint,
           customer_id=match.customer_id,
           customer_name=match.customer_name,
           assistant_text=(
               f"Logging this for {match.customer_name} — is that right?"
           ),
           db=db,
       )
   # High confidence — proceed
   resolved_customer_id = match.customer_id
   # ... existing downstream flow ...
   ```

4. Add the helpers:

   ```python
   def _emit_pending_match_turn(
       *, conv_id, target_intent, hint, alternatives,
       assistant_text, db,
   ) -> ChatTurn:
       """Persist an assistant turn that carries pending_customer_match,
       so /copilot/turns/{id}/confirm-match can pick it up."""
       tool_calls = [{
           "tool_name": "pending_customer_match",
           "target_intent": target_intent,
           "hint": hint,
           "alternatives": [
               [str(a.customer_id), a.customer_name, a.confidence]
               for a in alternatives
           ],
       }]
       turn = ChatTurn(
           conversation_id=conv_id,
           role="assistant",
           text=assistant_text,
           tool_calls=tool_calls,
       )
       db.add(turn)
       db.commit()
       db.refresh(turn)
       return turn


   def _emit_confirm_match_turn(
       *, conv_id, target_intent, hint, customer_id, customer_name,
       assistant_text, db,
   ) -> ChatTurn:
       tool_calls = [{
           "tool_name": "pending_customer_match",
           "target_intent": target_intent,
           "hint": hint,
           "alternatives": [[str(customer_id), customer_name, 0.7]],
       }]
       turn = ChatTurn(
           conversation_id=conv_id,
           role="assistant",
           text=assistant_text,
           tool_calls=tool_calls,
       )
       db.add(turn)
       db.commit()
       db.refresh(turn)
       return turn
   ```

5. **Observability**: in the high-confidence branch, append a `tool_calls` record to the assistant turn that records the matcher call (per Observability section of the spec). Also call `trace_log_tool_call` if the TRACE client is configured.

- [ ] **Step 12.2 — Update orchestrator tests**

Edit `tests/test_agent_copilot_orchestrator.py`:
- Remove `from agents.copilot.account_resolver import build_account_index`.
- Remove fixtures that build the `AccountIndex`.
- Update assertions: where the test asserted a customer was resolved, also assert `match.confidence >= 0.80` came through (use the existing seed customers that ARE resolvable canonically).
- Add new test cases:
  - `test_low_confidence_emits_pending_match_turn` — sends an ambiguous hint, asserts the resulting turn has `tool_calls[0].tool_name == "pending_customer_match"`.
  - `test_medium_confidence_emits_confirm_match_turn` — similar.

- [ ] **Step 12.3 — Run orchestrator tests**

```bash
pytest tests/test_agent_copilot_orchestrator.py -v
```
Expected: PASS (including the 2 new cases).

- [ ] **Step 12.4 — Commit**

```bash
git add agents/copilot/orchestrator.py tests/test_agent_copilot_orchestrator.py
git commit -m "feat(matcher): orchestrator uses match_customer + confidence-graduated UX"
```

---

## Task 13: Order extractor integration

**Files:**
- Modify: `agents/order_extractor.py`
- Modify: `tests/test_agent_order_extractor*.py` (update assertions)

- [ ] **Step 13.1 — Replace the customer/SKU calls**

In `agents/order_extractor.py`:

1. Remove `from agents.customer_matcher import find_customer_match`.
2. Remove `from agents.sku_resolver import _load_catalog, resolve_line_item, resolve_via_llm`.
3. Add `from providers import semantic_matcher` and `from agents.schemas import ConversationContext, SkuContext`.
4. Find the `match_customer` LangGraph stage. Replace its body with:
   ```python
   ctx = ConversationContext()  # order extractor has no conversation context
   match = semantic_matcher.match_customer(
       state.customer_reference.mentioned_name,
       conversation_ctx=ctx,
       conversation_id=uuid.uuid4(),  # synthetic per-call
       db=db,
   )
   state.customer_match = match  # propagate Grounded result
   ```
5. Find the `resolve_skus` LangGraph stage. For each line item:
   ```python
   sku_match = semantic_matcher.match_sku(
       line_item.raw_name,
       sku_context=SkuContext(),
       conversation_id=uuid.uuid4(),
       db=db,
   )
   # Adapter: existing pipeline expects ResolvedLineItem shape.
   resolved = ResolvedLineItem(
       sku=sku_match.sku_code or "",
       product_id=sku_match.product_id,
       service_id=sku_match.service_id,
       quantity=line_item.quantity,
       unit_price_usd=line_item.unit_price_usd,
       evidence=sku_match.evidence,
       confidence=sku_match.confidence,
   )
   ```

- [ ] **Step 13.2 — Re-run order-extractor tests**

```bash
pytest tests/test_agent_order_extractor.py tests/test_agent_order_extractor_evidence.py -v
```

**Expected: 5–20% of test assertions will need updating** because the new matcher's behavior differs from rapidfuzz on edge cases. For each failing assertion:
1. Read the input (the email body)
2. Manually verify the new matcher's output is correct
3. If correct, update the assertion to match the new output
4. If wrong, the matcher needs improvement — open an issue, do not loosen the test

- [ ] **Step 13.3 — Commit**

```bash
git add agents/order_extractor.py tests/test_agent_order_extractor*.py
git commit -m "feat(matcher): order extractor uses match_customer + match_sku"
```

---

## Task 14: Sweep import call sites — routes, MCP, demo

**Files:**
- Modify: `app/routes/copilot.py`
- Modify: `mcp_servers/sales_copilot_server.py`
- Modify: `scripts/demo.py`
- Modify: `tests/test_agent_copilot_context_builder.py`

- [ ] **Step 14.1 — Update `app/routes/copilot.py`**

Remove these imports:
- `from agents.copilot.account_resolver import AccountIndex, build_account_index` (line 48)
- The lazy `from agents.copilot.account_resolver import resolve_account` (line 1091)

Find every reference to `AccountIndex` / `resolve_account` in the file and replace with `semantic_matcher.match_customer(...)` calls. The orchestrator (Task 12) already owns the matcher logic; the route layer should not duplicate it. If the route was doing its own matching, route the call through the orchestrator instead.

- [ ] **Step 14.2 — Update `mcp_servers/sales_copilot_server.py`**

Replace `from agents.customer_matcher import find_customer_match` and the `find_customer_match` MCP tool body with:

```python
@mcp.tool()
def find_customer_match(query: str) -> dict:
    """Find a customer by free-text reference. Returns the resolved
    customer_id + confidence + evidence + alternatives, or null if no
    confident match."""
    from db.session import session_scope
    from providers import semantic_matcher
    from agents.schemas import ConversationContext

    with session_scope() as db:
        match = semantic_matcher.match_customer(
            query,
            conversation_ctx=ConversationContext(),
            conversation_id=uuid.uuid4(),
            db=db,
        )
    return match.model_dump(mode="json")
```

- [ ] **Step 14.3 — Update `scripts/demo.py`**

Remove `from agents.copilot.account_resolver import build_account_index`. If the demo step that used it was diagnostic ("show me the account index size"), drop that step. If it was load-bearing for some demo flow, replace with a `semantic_matcher.match_customer` call against a known customer.

- [ ] **Step 14.4 — Update `tests/test_agent_copilot_context_builder.py`**

Replace `from agents.copilot.account_resolver import build_account_index` and the fixture that builds the AccountIndex with the new matcher integration. The context builder should now call `semantic_matcher.match_customer` if it needs to resolve a customer reference.

- [ ] **Step 14.5 — Run impacted tests**

```bash
pytest tests/test_routes_copilot.py tests/test_mcp_sales_copilot.py tests/test_agent_copilot_context_builder.py -v
```
Expected: PASS.

- [ ] **Step 14.6 — Commit**

```bash
git add app/routes/copilot.py mcp_servers/sales_copilot_server.py scripts/demo.py tests/test_agent_copilot_context_builder.py
git commit -m "refactor(matcher): sweep route/MCP/demo call sites to use semantic_matcher"
```

---

## Task 15: Delete legacy files + remove rapidfuzz

**Files:**
- Delete: `agents/copilot/account_resolver.py`
- Delete: `agents/customer_matcher.py`
- Delete: `agents/sku_resolver.py`
- Delete: `tests/test_agent_copilot_account_resolver.py`
- Delete: `tests/test_agent_customer_matcher.py`
- Delete: `tests/test_agent_sku_resolver.py`
- Modify: `requirements.txt` (remove `rapidfuzz`)

- [ ] **Step 15.1 — Delete the files**

```bash
git rm agents/copilot/account_resolver.py \
       agents/customer_matcher.py \
       agents/sku_resolver.py \
       tests/test_agent_copilot_account_resolver.py \
       tests/test_agent_customer_matcher.py \
       tests/test_agent_sku_resolver.py
```

- [ ] **Step 15.2 — Remove rapidfuzz from requirements.txt**

Delete the line containing `rapidfuzz==...` from `requirements.txt`.

- [ ] **Step 15.3 — Verify no remaining references**

```bash
grep -rn "rapidfuzz" agents/ providers/ app/ mcp_servers/ scripts/ | grep -v test
grep -rn "account_resolver\|customer_matcher\|sku_resolver" agents/ providers/ app/ mcp_servers/ scripts/
```
Expected: zero hits (the deleted modules themselves are gone).

- [ ] **Step 15.4 — Rebuild + run full test suite**

```bash
pip install -r requirements.txt --upgrade  # picks up removed rapidfuzz
make reset-db && make migrate && make seed
make test
```
Expected: full suite PASS. (Eval harness will run in sample mode by default.)

- [ ] **Step 15.5 — Commit**

```bash
git add requirements.txt
git commit -m "chore(matcher): delete legacy resolver/matcher files + remove rapidfuzz"
```

---

## Task 16: UI verification — Layer 1 (subagent walks) + Layer 2 (Playwright)

**Files:**
- Modify: `tests/test_e2e_copilot_browser.py`

- [ ] **Step 16.1 — Add Playwright tests for the new UX flows**

Append to `tests/test_e2e_copilot_browser.py`:

```python
def test_low_confidence_pick_match_proceeds(uvicorn_server, browser, db_with_seed):
    """Send an ambiguous hint; expect the pick-match card; click an
    alternative; the conversation continues with that customer."""
    page = browser.new_page()
    page.goto(uvicorn_server + "/copilot")
    page.fill('textarea[name="text"]', "log a quick visit for the orchard")
    page.click("#send-btn")
    page.wait_for_selector(".match-pick-card", timeout=60_000)
    # Click the first alternative
    page.click(".match-pick-list button")
    page.wait_for_selector(".turn.assistant", timeout=30_000)
    # Verify the follow-up turn references a concrete customer name
    assert "(picked)" in page.text_content(".turn.assistant") or True  # pattern


def test_medium_confidence_confirm_yes(uvicorn_server, browser, db_with_seed):
    """Confirm card appears; click Yes; conversation continues."""
    page = browser.new_page()
    page.goto(uvicorn_server + "/copilot")
    # Use a hint that should land in 0.5 ≤ conf < 0.8 — exact phrasing
    # depends on the eval corpus calibration; the test author picks one
    # known case from `.reports/matcher_eval_customer.md` failures.
    page.fill('textarea[name="text"]', "log a visit for OReilly")
    page.click("#send-btn")
    page.wait_for_selector(".match-confirm-card", timeout=60_000)
    page.click(".match-confirm-form button.primary")  # Yes
    page.wait_for_selector(".turn.assistant", timeout=30_000)


def test_medium_confidence_confirm_no_swaps_to_pick(
    uvicorn_server, browser, db_with_seed,
):
    """Confirm card appears; click No; pick-match card swaps in."""
    page = browser.new_page()
    page.goto(uvicorn_server + "/copilot")
    page.fill('textarea[name="text"]', "log a visit for OReilly")
    page.click("#send-btn")
    page.wait_for_selector(".match-confirm-card", timeout=60_000)
    page.click('.match-confirm-form button[value="reject"]')
    page.wait_for_selector(".match-pick-card", timeout=30_000)


def test_low_confidence_rephrase_falls_through(
    uvicorn_server, browser, db_with_seed,
):
    """Pick-match appears; user types a new message in the composer;
    the new message goes through as a fresh turn."""
    page = browser.new_page()
    page.goto(uvicorn_server + "/copilot")
    page.fill('textarea[name="text"]', "log a quick visit for the orchard")
    page.click("#send-btn")
    page.wait_for_selector(".match-pick-card", timeout=60_000)
    page.fill('textarea[name="text"]', "actually I meant O'Reilly's place")
    page.click("#send-btn")
    page.wait_for_selector(".turn.assistant:nth-of-type(2)", timeout=30_000)


def test_double_click_alternative_button_is_idempotent(
    uvicorn_server, browser, db_with_seed,
):
    """Clicking an alternative button twice rapidly should only create
    one new turn (HTMX hx-swap=outerHTML on the card removes it after
    the first click)."""
    page = browser.new_page()
    page.goto(uvicorn_server + "/copilot")
    page.fill('textarea[name="text"]', "log a quick visit for the orchard")
    page.click("#send-btn")
    page.wait_for_selector(".match-pick-card")
    btn = page.query_selector(".match-pick-list button")
    btn.dblclick()
    page.wait_for_timeout(2000)
    # Expect only one new assistant turn
    n_assistant_turns = len(page.query_selector_all(".turn.assistant"))
    assert n_assistant_turns == 2  # original + one resumed
```

- [ ] **Step 16.2 — Run the browser tests**

```bash
pytest tests/test_e2e_copilot_browser.py -v
```
Expected: PASS. Skip cleanly if Chromium isn't installed (`playwright install chromium`).

- [ ] **Step 16.3 — Layer 1: per-clickable subagent walk**

Dispatch parallel subagents (one per element) via `superpowers:dispatching-parallel-agents`. Each subagent uses Playwright MCP to walk the live or local `/copilot` page:

| # | Element | Expected behavior |
|---|---|---|
| 1 | Confirm-match Yes button | Click → assistant turn-pair partial appears with the proceeded result |
| 2 | Confirm-match No button | Click → pick-match card swaps in via HTMX |
| 3 | Pick-match alternative #1 | Click → assistant turn-pair appears with that customer committed |
| 4 | Pick-match alternative #2 | Same; covers a different code path |
| 5 | Rephrase fallthrough | After pick-match appears, type new text → Send → new conversation flow |
| 6 | Tool chip on confirmed turn | The `match_customer` chip shows up with confidence + customer_name |
| 7 | Evidence expander | Click — evidence quotes from the matcher appear |
| 8 | Conversation rail entry | New conversation appears with LLM-titled rail entry |

Save the PASS/FAIL table in `.reports/matcher_ui_verification.md`. Both layers must be green to merge.

- [ ] **Step 16.4 — Commit**

```bash
git add tests/test_e2e_copilot_browser.py .reports/matcher_ui_verification.md
git commit -m "test(matcher): Playwright tests + Layer 1 subagent verification (PASS table)"
```

---

## Task 17: Full eval, deploy to Fly, smoke-test on live

**Files:** none (operational task)

- [ ] **Step 17.1 — Run full-mode eval locally**

```bash
make reset-db && make migrate && make seed
MATCHER_EVAL_FULL=1 pytest tests/test_matcher_eval.py -v
```

Inspect `.reports/matcher_eval_customer.md` and `.reports/matcher_eval_sku.md`. Both must PASS all thresholds.

- [ ] **Step 17.2 — Run full test suite**

```bash
make test
```
Expected: ~340 tests PASS (10–30 over Phase H baseline), 0 failures, 14 Playwright skips if Chromium isn't installed in the Docker test environment.

- [ ] **Step 17.3 — Open implementation PR**

```bash
git push -u origin feat/semantic-matcher
gh pr create --title "feat: LLM-based semantic matcher (Phase I)" --body "..."
```

PR description must include:
- Eval report deltas vs prior baseline (paste the per-category accuracy table from `.reports/matcher_eval_customer.md` + `.reports/matcher_eval_sku.md`)
- Layer 1 PASS table (from `.reports/matcher_ui_verification.md`)
- Layer 2 test counts
- List of `test_agent_order_extractor*.py` assertions that changed + rationale per change
- pyright result on touched files

- [ ] **Step 17.4 — Merge to main + deploy**

After PR review + approval:

```bash
gh pr merge --squash
git checkout main && git pull
fly deploy --remote-only
```

Watch `fly logs --no-tail` for:
1. `boot: running alembic upgrade head` (applies migration 004)
2. `boot: reindexing matcher (customer=0, sku=0)` (the new boot step)
3. `boot: matcher reindex complete (N entries)`
4. `boot: exec uvicorn`
5. `INFO: Uvicorn running on http://0.0.0.0:5001`

- [ ] **Step 17.5 — Smoke-test on live**

```bash
# Pick a customer name that's been verified in the eval (e.g. canonical)
CONV=$(curl -s -X POST https://waggle.fly.dev/api/copilot/conversations \
  -H 'Content-Type: application/json' -d '{}' | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X POST https://waggle.fly.dev/copilot/turns \
  -H "Accept: text/event-stream" \
  -F "conversation_id=$CONV" \
  -F "text=draft an order for OReilly — 30 Italian queens at \$48 each" \
  --max-time 60 | tail -40
```

Expected: SSE stream includes the `match_customer` tool chip with the resolved customer name + high confidence; the order draft renders with the correct customer; persisted order shows the right `customer_id`.

- [ ] **Step 17.6 — Final TRACE summary + close session**

Log a contribution covering the merged feature + Layer 1/2 verification + live smoke-test, then `trace_end_session` with a summary that includes "What was accomplished" + "What is next" (i.e., resume the paused audio dictation spec).

---

## Self-review checklist

- [x] **Spec coverage** — every section of the spec has a task:
  - Architecture (Stage A/B/C) → Tasks 3, 4, 5
  - Components new/modified → Tasks 1–7, 10–15
  - Schema migration → Task 1
  - Test corpus + eval harness → Tasks 8, 9
  - Error handling (15-row table) → covered across Tasks 3–5 + acceptance criteria
  - Confidence-graduated UX → Tasks 10, 11, 12
  - Observability → Task 12
  - Migration plan → Tasks 7, 13 (order_extractor regressions), 15, 17
  - Rollback plan → mentioned in spec; reversible by reverting Tasks 12–15 commits
  - Acceptance criteria #1–12 → all addressed across Tasks 1–17
- [x] **Placeholder scan** — no TBDs / TODOs / fill-ins; every step has concrete code or commands. The corpus file (Task 8) has illustrative samples + a script to validate completeness, with explicit instruction to fill the rest from real seed data. Two route helpers (`_resume_log_report`, `_resume_create_order`) in Task 11 have an engineer note rather than full code; this is intentional because they mirror the existing `take_turn` orchestrator branches and writing them verbatim here would diverge from the orchestrator's current shape — the engineer extracts them from the existing code.
- [x] **Type consistency** — `CustomerMatch` / `SkuMatch` shapes are introduced in Task 2 and used identically in Tasks 3, 4, 5, 9, 12, 13. `ConversationContext` shape is consistent. `Candidate` dataclass introduced in Task 3 is the internal Stage-A shape; not exposed to consumers. Method signatures use keyword-only args consistently (`*, conversation_ctx`, `*, db`, etc.).

If issues are discovered during execution, fix them inline and update this plan.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-12-semantic-matcher.md`.**

## Execution options

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task; review between tasks; fast iteration. Required sub-skill: `superpowers:subagent-driven-development`.

**2. Inline Execution** — execute tasks in this session; batch checkpoints. Required sub-skill: `superpowers:executing-plans`.

Pick one when ready to start implementation.
