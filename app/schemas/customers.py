"""Customer response models."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Pagination
from db.enums import MarketSegment


class CustomerOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    name: str
    erp_customer_no: str
    market_segment: MarketSegment
    aliases: list[str] = Field(default_factory=list)
    site_count: int = Field(
        default=0,
        ge=0,
        description="Number of customer_sites belonging to this customer.",
    )


CustomerListOut = Pagination[CustomerOut]
