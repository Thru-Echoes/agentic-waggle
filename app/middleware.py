"""HTTP middleware: per-request context, security headers, and CSRF origin checks.

Two middlewares, both registered in ``app.main.create_app``:

``RequestContextMiddleware``
    Assigns a request id (reusing ``Fly-Request-Id`` / ``X-Request-ID`` when
    a proxy supplied one) and a Content-Security-Policy nonce, exposes both
    on ``request.state``, echoes the id as ``X-Request-ID``, and adds the
    standard security headers to every response. Swagger's assets are served
    from a third-party CDN with inline scripts, so ``/docs``, ``/redoc`` and
    ``/openapi.json`` get every header except the CSP.

``CsrfOriginMiddleware``
    Rejects unsafe-method requests (POST/PUT/PATCH/DELETE) that a browser
    marks as cross-site. HTTP Basic credentials are sent ambiently like
    cookies, so without this a page on another origin could submit the
    HTMX forms on behalf of a logged-in reviewer. The check uses Fetch
    Metadata (``Sec-Fetch-Site``) with ``Origin`` as the fallback; requests
    carrying neither header (curl, the test client, MCP tooling) are not
    browser requests and pass.

Side effects: none at import time. Per request, both middlewares may
short-circuit with a response instead of calling the next handler.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.errors import GENERIC_500_DETAIL, problem_response
from app.schemas.common import ProblemDetail

log = logging.getLogger(__name__)

#: Paths whose pages embed third-party, inline-script assets (Swagger UI,
#: ReDoc). They keep every other security header.
CSP_EXEMPT_PATHS: frozenset[str] = frozenset(
    {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)

UNSAFE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

HSTS_VALUE = "max-age=31536000; includeSubDomains"


def get_csp_header(nonce: str) -> str:
    """Return the Content-Security-Policy value for one response.

    Scripts run only from this origin or when tagged with the per-request
    nonce; styles come from this origin only (no inline ``style=``
    attributes exist in the templates, and HTMX's injected indicator
    stylesheet is disabled via its meta config); images may be ``data:``
    URIs for the SVG logo; the app may not be framed.
    """
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )


def _is_forwarded_https(request: Request) -> bool:
    """True when the request arrived over TLS, directly or via a proxy."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id + CSP nonce on ``request.state``; security headers on the way out."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = (
            request.headers.get("fly-request-id")
            or request.headers.get("x-request-id")
            or uuid.uuid4().hex[:16]
        )
        nonce = secrets.token_urlsafe(16)
        request.state.request_id = request_id
        request.state.csp_nonce = nonce

        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 — logged with the id, rendered generically, never swallowed
            # Starlette's outermost error middleware would otherwise render
            # this below us, without our headers. Render it here so even a
            # 500 carries the request id and the security headers; the
            # traceback goes to the log under the same id.
            log.exception("unhandled exception on %s %s [%s]", request.method, request.url, request_id)
            response = problem_response(
                request, status=500, title="Internal server error", detail=GENERIC_500_DETAIL
            )

        headers = response.headers
        headers["X-Request-ID"] = request_id
        headers["X-Content-Type-Options"] = "nosniff"
        headers["X-Frame-Options"] = "DENY"
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path not in CSP_EXEMPT_PATHS:
            headers["Content-Security-Policy"] = get_csp_header(nonce)
        if _is_forwarded_https(request):
            headers["Strict-Transport-Security"] = HSTS_VALUE
        return response


def is_cross_site_request(request: Request) -> bool:
    """Decide whether a browser sent this unsafe-method request cross-site.

    Returns True (reject) when ``Sec-Fetch-Site`` is ``cross-site`` or
    ``same-site`` (a sibling subdomain is not this app), or when it is absent
    and ``Origin`` names a different host. Returns False when the browser
    says ``same-origin`` / ``none`` (typed URL, bookmark) or when neither
    header is present (not a browser).

    Side effects: none.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None:
        return False
    origin_host = origin.split("://", 1)[-1].rstrip("/")
    return origin_host.lower() != (request.headers.get("host") or "").lower()


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Reject cross-site unsafe-method requests with a 403 problem detail."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method in UNSAFE_METHODS and is_cross_site_request(request):
            problem = ProblemDetail(
                title="Cross-site request rejected",
                status=403,
                detail=(
                    "State-changing requests must originate from this site. "
                    "The browser reported a different origin."
                ),
                instance=str(request.url),
                request_id=getattr(request.state, "request_id", None),
            )
            return JSONResponse(status_code=403, content=problem.model_dump())
        return await call_next(request)


__all__ = [
    "CSP_EXEMPT_PATHS",
    "CsrfOriginMiddleware",
    "RequestContextMiddleware",
    "get_csp_header",
    "is_cross_site_request",
]
