"""Vector-search request + response models."""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SourceType = Literal[
    "customer_report",
    "email",
    "product_data_sheet",
    "regulatory_doc",
]


class SearchRequest(BaseModel):
    """Hybrid (semantic + structured filter) search request.

    Every filter is additive AND. Cosine similarity ranks the survivors.
    """

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=50)

    # -- structured filters (all optional, AND-combined) ------------------
    source_type: SourceType | None = Field(
        default=None,
        description=(
            "Restrict to chunks from this document type. Useful for "
            "scoping a question to inspection reports vs emails vs "
            "regulatory references."
        ),
    )
    site_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Optional site filter — only customer-report chunks from this "
            "site are returned. Other source types are excluded when set."
        ),
    )
    visit_date_after: str | None = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD). Customer-report chunks with visit_date "
            "before this date are excluded."
        ),
    )
    min_varroa: float | None = Field(
        default=None,
        ge=0.0,
        le=20.0,
        description=(
            "Threshold filter — return only chunks whose parent report "
            "has varroa_count >= this value."
        ),
    )
    tag: str | None = Field(
        default=None,
        description=(
            "Faceted filter — return only chunks whose tags array "
            "contains this tag (e.g., 'regulatory_alert', 'queen_failure')."
        ),
    )


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    content: str
    score: float = Field(..., ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    hits: list[SearchHit]
