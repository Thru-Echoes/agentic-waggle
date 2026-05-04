"""Vector-search request + response models."""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=50)
    site_id: uuid.UUID | None = Field(
        default=None,
        description="Optional site filter; only chunks from this site are returned.",
    )
    visit_date_after: str | None = Field(
        default=None,
        description="ISO date (YYYY-MM-DD). Chunks from visits before this date are excluded.",
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
