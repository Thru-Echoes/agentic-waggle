"""Pydantic I/O models shared across Waggle's MCP servers.

MCP tools return typed Pydantic models; fastmcp serializes them to JSON on
the wire. Keeping these in one place makes schemas discoverable and prevents
accidental drift between the two servers.

Money / measurement fields are typed as `float` on purpose: fastmcp's JSON
schema generator (backed by Rust `regex`) doesn't support Pydantic v2's
native `Decimal` validator (which uses a negative lookahead). `float` is
JSON-native and the precision loss is acceptable for MCP surface — inside
the app, `db.models` still stores these as `Numeric`/`Decimal`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# sales_copilot tool shapes
# ---------------------------------------------------------------------------


class CustomerHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_id: uuid.UUID
    name: str
    erp_customer_no: str
    market_segment: str
    score: float = Field(..., ge=0.0, le=100.0)


class SiteSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: uuid.UUID
    customer_id: uuid.UUID
    name: str
    acreage_by_crop: dict[str, int]


class OrderSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    apiarist_name: str
    state: str
    total_usd: float
    pollination_date: date | None = None
    created_at: datetime


class CustomerReportSnippet(BaseModel):
    """A search-result snippet from the vector store. Not the full report."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    content: str
    score: float = Field(..., ge=0.0, le=1.0)
    site_name: str | None = None
    visit_date: date | None = None


class DraftedEmail(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    body_text: str
    tone: Literal["professional", "warm", "urgent"]


# ---------------------------------------------------------------------------
# customer_reports tool shapes
# ---------------------------------------------------------------------------


class ExtractedCustomerReport(BaseModel):
    """LLM-extracted structured fields from a free-text narrative."""

    model_config = ConfigDict(frozen=True)

    hives_placed: int = Field(..., ge=0, le=2000)
    varroa_count: float = Field(..., ge=0.0, le=50.0)
    diseases_observed: list[
        Literal["AFB", "EFB", "NOSEMA", "SHB", "TROPILAELAPS", "NONE"]
    ] = Field(default_factory=list)
    pesticide_exposure_note: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class CustomerReportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_report_id: uuid.UUID
    customer_name: str
    site_name: str
    author_name: str
    visit_date: date
    hives_placed: int
    varroa_count: float
    diseases_observed: list[str]


class FleetHealthSummary(BaseModel):
    """Aggregate view of hive health across a region or the whole fleet."""

    model_config = ConfigDict(frozen=True)

    scope: str = Field(..., description="Region code or 'fleet'.")
    reports_considered: int = Field(..., ge=0)
    avg_varroa: float
    max_varroa: float
    disease_counts: dict[str, int] = Field(default_factory=dict)
    sites_with_varroa_above_3: int = Field(..., ge=0)
