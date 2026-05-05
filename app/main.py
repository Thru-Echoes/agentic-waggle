"""Waggle FastAPI application factory.

Entry point for uvicorn. Builds and returns the `FastAPI` app with all
routers mounted, startup/shutdown hooks registered, and a global
RFC 7807-style exception handler that maps unhandled exceptions to
`ProblemDetail` JSON responses.

Side effects at import time: instantiates `FastAPI`, imports every route
module (which in turn imports db.models and providers). No network or DB
calls yet.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import BasicAuthMiddleware
from app.config import Settings, get_settings
from app.routes import agent, customers, health, orders, pages, search, customer_reports
from app.schemas.common import ProblemDetail

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


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
    )

    # Production gate: HTTP Basic auth when WAGGLE_USER + WAGGLE_PASS are set
    # (typically as Fly secrets). No-op for local dev. Public paths
    # (/health, /ready, /static/*) bypass the check.
    app.add_middleware(BasicAuthMiddleware)

    # Mount static assets (Waggle CSS, logo SVG, etc).
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

    # Server-rendered HTML at root.
    app.include_router(pages.router)

    @app.exception_handler(Exception)
    async def _problem_detail_handler(request: Request, exc: Exception) -> JSONResponse:
        """Convert unhandled exceptions to RFC-7807 ProblemDetail JSON.

        Side effect: logs the exception at ERROR level before returning 500.
        Known HTTPExceptions are left to FastAPI's default handler via re-raise
        semantics — this only catches truly unhandled errors.
        """
        from fastapi.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            problem = ProblemDetail(
                title="Request failed",
                status=exc.status_code,
                detail=str(exc.detail),
                instance=str(request.url),
            )
            return JSONResponse(
                status_code=exc.status_code,
                content=problem.model_dump(),
            )

        log.exception("unhandled exception on %s %s", request.method, request.url)
        problem = ProblemDetail(
            title="Internal server error",
            status=500,
            detail=str(exc),
            instance=str(request.url),
        )
        return JSONResponse(status_code=500, content=problem.model_dump())

    return app


app = create_app()
