"""Request / response Pydantic models for Waggle routes.

Separate from `db.models` because an API boundary is not the same thing as
a database row — response shapes are driven by what the client needs, not
by what the table stores. Converting between the two happens in the route
handlers.

Convention:
    * `<Resource>Out`  — response model (read path)
    * `<Resource>In`   — request body model (write path, when it exists)
    * `<Resource>ListOut` — paginated list response with `items + total`
"""
from __future__ import annotations

from app.schemas.common import Pagination, ProblemDetail
from app.schemas.copilot import (
    ChatTurnIn,
    ChatTurnOut,
    ConfirmResultOut,
    ConversationCreateIn,
    ConversationListItem,
    ConversationListOut,
    ConversationOut,
    DraftConfirmIn,
)
from app.schemas.customers import CustomerListOut, CustomerOut
from app.schemas.orders import OrderLineItemOut, OrderListOut, OrderOut
from app.schemas.search import SearchHit, SearchRequest, SearchResponse
from app.schemas.customer_reports import CustomerReportListOut, CustomerReportOut

__all__ = [
    "ChatTurnIn",
    "ChatTurnOut",
    "ConfirmResultOut",
    "ConversationCreateIn",
    "ConversationListItem",
    "ConversationListOut",
    "ConversationOut",
    "CustomerListOut",
    "CustomerOut",
    "DraftConfirmIn",
    "OrderLineItemOut",
    "OrderListOut",
    "OrderOut",
    "Pagination",
    "ProblemDetail",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "CustomerReportListOut",
    "CustomerReportOut",
]
