"""Trip-report response models."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Pagination


class CustomerReportOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    customer_site_id: uuid.UUID
    site_name: str
    customer_name: str
    author_id: uuid.UUID
    author_name: str
    visit_date: date
    hives_placed: int = Field(..., ge=0)
    varroa_count: Decimal
    diseases_observed: list[str] = Field(default_factory=list)
    pesticide_exposure_note: str | None = None
    narrative: str


CustomerReportListOut = Pagination[CustomerReportOut]
