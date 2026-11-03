"""Failure rendering shared by the middleware and the exception handlers.

``problem_response`` turns a failure into either an RFC 7807 JSON body or,
for HTMX requests (``HX-Request: true``), an HTML error card that swaps into
the element the request came from. Both carry the request id so a reader
can find the server log line.

Side effects: none (pure rendering); callers do the logging.
"""
from __future__ import annotations

from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import Request

from app.schemas.common import ProblemDetail
from app.templating import get_templates

GENERIC_500_DETAIL = "Something went wrong on the server. Quote the request id when reporting it."


def is_htmx_request(request: Request) -> bool:
    """True when the request came from an HTMX trigger (a swap target exists)."""
    return request.headers.get("hx-request", "").lower() == "true"


def problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    hint: str | None = None,
) -> Response:
    """Render a failure as JSON problem details or, for HTMX, an HTML card."""
    request_id = getattr(request.state, "request_id", None)
    if is_htmx_request(request):
        html = get_templates().get_template("partials/error_card.html").render(
            title=title, detail=detail, hint=hint, request_id=request_id, status=status
        )
        return HTMLResponse(html, status_code=status)
    problem = ProblemDetail(
        title=title,
        status=status,
        detail=detail,
        instance=str(request.url),
        request_id=request_id,
    )
    return JSONResponse(status_code=status, content=problem.model_dump())


__all__ = ["GENERIC_500_DETAIL", "is_htmx_request", "problem_response"]
