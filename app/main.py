"""Waggle FastAPI application factory.

Entry point for uvicorn. Builds and returns the `FastAPI` app with all
routers mounted, the middleware stack registered, startup hooks, and the
exception handlers that turn failures into something a reader can act on.

Middleware order (outermost first): request context + security headers,
HTTP Basic auth, LLM rate limit + daily budget, visitor cookie, cross-site
origin check. Security headers therefore apply
to 401 and 403 responses too.

Exception handling:

* ``HTTPException`` → RFC 7807 problem detail with the handler's message.
* LLM-provider failures (bad key, rate limit, outage, unreachable) → 503
  with a fixed user-facing phrase; the provider's own message goes to the
  log only, because it can carry key fragments and account URLs.
* Anything else → 500 with a generic message and the request id; the full
  traceback is logged under that id.

HTMX requests (``HX-Request: true``) get an HTML error card instead of
JSON so the failure is visible in the page they came from.

Side effects at import time: instantiates `FastAPI`, imports every route
module (which in turn imports db.models and providers). No network or DB
calls yet.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.auth import BasicAuthMiddleware
from app.config import Settings, get_settings
from app.middleware import CsrfOriginMiddleware, RequestContextMiddleware
from app.ratelimit import LlmRateLimiter, LlmRateLimitMiddleware
from app.visitor import VisitorIdMiddleware
from app.routes import (
    agent,
    copilot,
    customers,
    customer_reports,
    health,
    orders,
    pages,
    search,
)
from app.errors import GENERIC_500_DETAIL, problem_response
from providers.llm import describe_provider_error, get_provider_error_types, get_provider_status

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Background tasks kept alive past their scheduling scope (asyncio only
#: holds weak refs to tasks; without this they can be GC'd mid-run).
_bg_tasks: set[asyncio.Task] = set()


async def _run_boot_reindex() -> None:
    """Backfill the matcher search indexes AFTER the server is accepting
    requests, so a cold deploy binds the port immediately instead of
    blocking on the synchronous one-by-one embed pass (the prior
    "not listening on 0.0.0.0:5001" deploy fragility).

    Runs only when ``WAGGLE_BOOT_REINDEX=1`` — set by ``scripts/boot.py``
    in the container, never under tests. Fail-loud: logs at ERROR if it
    raises (matcher calls then raise ``MatcherIndexEmpty`` until the
    index is populated — loud + expected, not a silent degrade).

    Side effects: opens a DB session; may TRUNCATE+rebuild the two
    matcher search tables.
    """
    from db.session import session_scope
    from scripts.boot import reindex_if_empty

    def _work() -> None:
        with session_scope() as db:
            reindex_if_empty(db)

    try:
        await asyncio.to_thread(_work)
    except Exception:
        log.exception(
            "boot reindex failed; matcher calls will raise "
            "MatcherIndexEmpty until the index is populated"
        )


async def _run_provider_probe(settings: Settings) -> None:
    """Probe the LLM provider once at startup so a dead key is loud in the logs.

    The result is cached for ``/ready``. Side effects: one metadata request
    to the provider; an ERROR log line when it fails.
    """
    try:
        status = await asyncio.to_thread(
            get_provider_status, ttl_seconds=settings.llm_probe_ttl_seconds, force=True
        )
    except Exception:
        log.exception("LLM provider probe crashed")
        return
    if status.ok:
        log.info("LLM provider probe: %s (%s, %s)", status.detail, status.provider, status.model)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Schedule post-bind background work: matcher reindex and provider probe.

    The ASGI lifespan startup completes BEFORE uvicorn accepts
    connections, so both tasks are fire-and-forget (scheduled, not
    awaited) — the server starts serving immediately.
    """
    settings = get_settings()
    if os.environ.get("WAGGLE_BOOT_REINDEX") == "1":
        task = asyncio.create_task(_run_boot_reindex())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    if os.environ.get("WAGGLE_SKIP_PROVIDER_PROBE") != "1":
        probe = asyncio.create_task(_run_provider_probe(settings))
        _bg_tasks.add(probe)
        probe.add_done_callback(_bg_tasks.discard)
    yield


def create_app() -> FastAPI:
    """Build and return the Waggle FastAPI app. Pure factory, no globals."""
    settings: Settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = FastAPI(
        title=f"{settings.product_name} — {settings.tagline}",
        description=(
            f"{settings.product_name} is an agentic, MCP-native CRM for "
            f"{settings.company_name}. Local-first; runs entirely on your laptop."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )

    # Middleware. Starlette wraps in registration order, so the last one
    # added runs first; register innermost first. The rate limiter sits
    # inside auth so unauthenticated requests never consume the budget.
    limiter = LlmRateLimiter(
        per_minute=settings.waggle_llm_rate_per_minute,
        daily_budget=settings.waggle_llm_daily_budget,
    )
    app.state.llm_limiter = limiter
    app.add_middleware(CsrfOriginMiddleware)
    app.add_middleware(VisitorIdMiddleware)
    app.add_middleware(LlmRateLimitMiddleware, limiter=limiter)
    app.add_middleware(BasicAuthMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # Mount static assets (Waggle CSS, logo SVG, vendored HTMX).
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Liveness/readiness probes stay at root.
    app.include_router(health.router)

    # JSON API namespaced under /api so it doesn't shadow the HTML pages,
    # which share the same root-level paths (e.g., /customers HTML and
    # /api/customers JSON).
    app.include_router(customers.router, prefix="/api")
    app.include_router(orders.router, prefix="/api")
    app.include_router(customer_reports.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(agent.router, prefix="/api")
    app.include_router(copilot.api_router, prefix="/api")

    # Server-rendered HTML at root.
    app.include_router(pages.router)
    app.include_router(copilot.html_router)

    # Template-rendering routes used only by tests. Off unless explicitly
    # enabled so production exposes nothing it does not serve to users.
    if settings.waggle_internal_routes_enabled:
        app.include_router(copilot.internal_router)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> Response:
        """Known HTTP errors keep their handler-authored message."""
        return problem_response(
            request, status=exc.status_code, title="Request failed", detail=str(exc.detail)
        )

    async def _provider_error_handler(request: Request, exc: Exception) -> Response:
        """LLM-provider failures: fixed phrase to the client, full text to the log.

        Side effect: logs at ERROR with the request id.
        """
        log.error(
            "LLM provider failure on %s %s [%s]: %s: %s",
            request.method,
            request.url.path,
            getattr(request.state, "request_id", "-"),
            type(exc).__name__,
            exc,
        )
        return problem_response(
            request,
            status=503,
            title="LLM provider unavailable",
            detail=describe_provider_error(exc),
            hint="The rest of the app keeps working; only the agent and copilot need the model.",
        )

    for error_type in get_provider_error_types():
        app.add_exception_handler(error_type, _provider_error_handler)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
        """Last resort for exceptions raised outside the middleware stack.

        ``RequestContextMiddleware`` renders unhandled exceptions from the
        routes (so they carry headers and the request id); this handler
        covers the rare case of a failure in the middleware itself. Same
        contract: generic message, traceback in the log, no exception text.
        """
        log.exception(
            "unhandled exception outside the middleware stack on %s %s",
            request.method,
            request.url,
        )
        return problem_response(
            request, status=500, title="Internal server error", detail=GENERIC_500_DETAIL
        )

    return app


app = create_app()
