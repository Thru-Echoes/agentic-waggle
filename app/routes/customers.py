"""Customer read routes — list + detail."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlmodel import Session

from app.schemas.customers import CustomerListOut, CustomerOut
from db.models import Customer, CustomerSite
from db.session import get_db_session

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=CustomerListOut)
def list_customers(
    db: Session = Depends(get_db_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    market_segment: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Case-insensitive name filter."),
) -> CustomerListOut:
    """List customers with optional market-segment or name filter."""
    base_q = select(Customer)
    if market_segment is not None:
        base_q = base_q.where(Customer.market_segment == market_segment)
    if q:
        base_q = base_q.where(Customer.name.ilike(f"%{q}%"))  # type: ignore[attr-defined]

    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()

    rows = db.execute(
        base_q.order_by(Customer.name).offset(offset).limit(limit)
    ).scalars().all()

    # Attach site counts in one query rather than N+1.
    counts_q = (
        select(CustomerSite.customer_id, func.count(CustomerSite.id))
        .where(CustomerSite.customer_id.in_([c.id for c in rows]))  # type: ignore[attr-defined]
        .group_by(CustomerSite.customer_id)
    )
    counts: dict[uuid.UUID, int] = {
        cid: n for cid, n in db.execute(counts_q).all()
    }

    items = [
        CustomerOut(
            id=c.id,
            name=c.name,
            erp_customer_no=c.erp_customer_no,
            market_segment=c.market_segment,
            aliases=c.aliases,
            site_count=counts.get(c.id, 0),
        )
        for c in rows
    ]
    return CustomerListOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db_session),
) -> CustomerOut:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"customer {customer_id} not found")
    site_count = db.execute(
        select(func.count(CustomerSite.id)).where(
            CustomerSite.customer_id == customer_id
        )
    ).scalar_one()
    return CustomerOut(
        id=customer.id,
        name=customer.name,
        erp_customer_no=customer.erp_customer_no,
        market_segment=customer.market_segment,
        aliases=customer.aliases,
        site_count=site_count,
    )
