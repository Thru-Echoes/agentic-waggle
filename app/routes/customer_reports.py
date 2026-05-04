"""Trip-report read routes — list + detail."""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlmodel import Session

from app.schemas.customer_reports import CustomerReportListOut, CustomerReportOut
from db.models import CustomerReport
from db.session import get_db_session

router = APIRouter(prefix="/customer-reports", tags=["customer_reports"])


def _to_out(tr: CustomerReport) -> CustomerReportOut:
    return CustomerReportOut(
        id=tr.id,
        customer_site_id=tr.customer_site_id,
        site_name=tr.site.name if tr.site else "(unknown)",
        customer_name=(
            tr.site.customer.name if tr.site and tr.site.customer else "(unknown)"
        ),
        author_id=tr.author_id,
        author_name=tr.author.name if tr.author else "(unknown)",
        visit_date=tr.visit_date,
        hives_placed=tr.hives_placed,
        varroa_count=tr.varroa_count,
        diseases_observed=tr.diseases_observed,
        pesticide_exposure_note=tr.pesticide_exposure_note,
        narrative=tr.narrative,
    )


@router.get("", response_model=CustomerReportListOut)
def list_customer_reports(
    db: Session = Depends(get_db_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    customer_site_id: uuid.UUID | None = Query(default=None),
    min_varroa: Decimal | None = Query(
        default=None,
        description="Filter to reports with varroa_count >= this value.",
    ),
) -> CustomerReportListOut:
    base_q = select(CustomerReport)
    if customer_site_id is not None:
        base_q = base_q.where(CustomerReport.customer_site_id == customer_site_id)
    if min_varroa is not None:
        base_q = base_q.where(CustomerReport.varroa_count >= min_varroa)

    total = db.execute(
        select(func.count()).select_from(base_q.subquery())
    ).scalar_one()

    rows = db.execute(
        base_q.options(
            selectinload(CustomerReport.site),
            selectinload(CustomerReport.author),
        )
        .order_by(CustomerReport.visit_date.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    return CustomerReportListOut(
        items=[_to_out(tr) for tr in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{customer_report_id}", response_model=CustomerReportOut)
def get_customer_report(
    customer_report_id: uuid.UUID,
    db: Session = Depends(get_db_session),
) -> CustomerReportOut:
    row = db.execute(
        select(CustomerReport)
        .where(CustomerReport.id == customer_report_id)
        .options(
            selectinload(CustomerReport.site),
            selectinload(CustomerReport.author),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"customer_report {customer_report_id} not found"
        )
    return _to_out(row)
