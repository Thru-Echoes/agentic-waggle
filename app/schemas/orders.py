"""Order + line-item response models."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Pagination
from db.enums import OrderState


class OrderLineItemOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    sku: str
    quantity: int = Field(..., ge=1)
    unit_price_usd: Decimal
    product_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    apiarist_id: uuid.UUID
    apiarist_name: str
    state: OrderState
    pollination_date: date | None = None
    total_usd: Decimal
    line_items: list[OrderLineItemOut] = Field(default_factory=list)
    source_email_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


OrderListOut = Pagination[OrderOut]
