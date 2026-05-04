"""Shared response shapes used across resources.

`Pagination` is the envelope for list responses; `ProblemDetail` is the
RFC 7807 problem-details shape used by the app's exception handler.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class Pagination(BaseModel, Generic[ItemT]):
    """Generic list envelope.

    Set the concrete item type at each use site, e.g.
    `Pagination[CustomerOut]`. Pydantic v2 handles the generic parameterization.
    """

    model_config = ConfigDict(frozen=True)

    items: list[ItemT] = Field(..., description="Page of results.")
    total: int = Field(..., ge=0, description="Total matching rows across all pages.")
    limit: int = Field(..., ge=1, le=200, description="Page size.")
    offset: int = Field(..., ge=0, description="Row offset.")


class ProblemDetail(BaseModel):
    """RFC 7807 problem-details response body."""

    model_config = ConfigDict(frozen=True)

    type: str = Field(
        default="about:blank",
        description="URI reference identifying the problem type.",
    )
    title: str = Field(..., description="Short human-readable summary.")
    status: int = Field(..., ge=400, le=599)
    detail: str | None = Field(
        default=None, description="Human-readable explanation specific to this occurrence."
    )
    instance: str | None = Field(
        default=None, description="URI reference identifying this specific occurrence."
    )
