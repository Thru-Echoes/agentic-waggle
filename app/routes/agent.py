"""Agent-extraction route.

`POST /api/agent/extract` takes an `EmailInput`, runs the LangGraph pipeline,
and returns either an `OrderDraft` or an `ExtractionFailure`. Stateless —
the draft is NOT persisted by this route. The HTML review UI lives at
`/inbox` (see `app/routes/pages.py::inbox_extract`); the confirm-and-persist
step that would write an `orders` row is still open work.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from agents.order_extractor import run_order_extraction
from agents.schemas import ExtractionFailure, OrderDraft
from app.schemas.agent import ExtractOrderRequest, ExtractOrderResponse
from db.session import get_db_session

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/extract", response_model=ExtractOrderResponse)
def extract_order(
    request: ExtractOrderRequest,
    db: Session = Depends(get_db_session),
) -> ExtractOrderResponse:
    """Run the LangGraph order-extraction pipeline on an email.

    Never persists. The response always succeeds at the HTTP level; check
    `ok` + `draft` / `failure` to distinguish.
    """
    result = run_order_extraction(request.email, db)
    if isinstance(result, OrderDraft):
        return ExtractOrderResponse(ok=True, draft=result)
    assert isinstance(result, ExtractionFailure)
    return ExtractOrderResponse(ok=False, failure=result)
