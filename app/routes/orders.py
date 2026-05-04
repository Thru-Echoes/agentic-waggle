"""Order read routes — list + detail, with customer/apiarist join."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlmodel import Session

from app.schemas.orders import OrderLineItemOut, OrderListOut, OrderOut
from db.enums import OrderState
from db.models import Order
from db.session import get_db_session

router = APIRouter(prefix="/orders", tags=["orders"])


def _to_out(order: Order) -> OrderOut:
    """Coerce an Order ORM row (with relationships loaded) into OrderOut."""
    return OrderOut(
        id=order.id,
        customer_id=order.customer_id,
        customer_name=order.customer.name if order.customer else "(unknown)",
        apiarist_id=order.user_id,
        apiarist_name=order.apiarist.name if order.apiarist else "(unknown)",
        state=order.state,
        pollination_date=order.pollination_date,
        total_usd=order.total_usd,
        line_items=[
            OrderLineItemOut(
                id=li.id,
                sku=li.sku,
                quantity=li.quantity,
                unit_price_usd=li.unit_price_usd,
                product_id=li.product_id,
                service_id=li.service_id,
            )
            for li in order.line_items
        ],
        source_email_id=order.source_email_id,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.get("", response_model=OrderListOut)
def list_orders(
    db: Session = Depends(get_db_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    state: OrderState | None = Query(default=None),
    customer_id: uuid.UUID | None = Query(default=None),
) -> OrderListOut:
    base_q = select(Order)
    if state is not None:
        base_q = base_q.where(Order.state == state)
    if customer_id is not None:
        base_q = base_q.where(Order.customer_id == customer_id)

    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()

    rows = db.execute(
        base_q.options(
            selectinload(Order.customer),
            selectinload(Order.apiarist),
            selectinload(Order.line_items),
        )
        .order_by(Order.created_at.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    return OrderListOut(
        items=[_to_out(o) for o in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: uuid.UUID,
    db: Session = Depends(get_db_session),
) -> OrderOut:
    row = db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.apiarist),
            selectinload(Order.line_items),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"order {order_id} not found")
    return _to_out(row)
