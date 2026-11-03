"""Health + readiness routes.

`/health` is a liveness probe (process is up; carries build provenance).
`/ready` checks the DB is reachable, pgvector is installed, and reports the
LLM-provider probe. Both return JSON and are the only unauthenticated
routes besides static assets.

``/ready`` stays 200 while the database works: the pages that do not need
the model still serve. A failed provider probe is reported as
``status: "degraded"`` with the reason, and the same probe logs at ERROR
at startup and on each refresh, so a dead key is visible in the logs and
to anyone polling the endpoint.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlmodel import Session

from app.config import Settings, get_settings
from db.session import get_db_session
from providers.llm import ProviderState, get_provider_status

router = APIRouter(tags=["health"])


class HealthOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    product: str
    tagline: str
    version: str = Field(description="Application version string.")
    git_sha: str = Field(description="Commit the image was built from (WAGGLE_GIT_SHA), or 'unknown'.")
    built_at: str = Field(description="Image build time, ISO-8601 UTC (WAGGLE_BUILT_AT), or 'unknown'.")


class ReadyOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = Field(description="'ready' when every dependency is fine, 'degraded' otherwise.")
    database: str
    pgvector: str
    llm: ProviderState = Field(description="Result of the cached LLM-provider auth probe.")
    llm_detail: str = Field(description="Fixed user-facing phrase for the probe result.")
    llm_model: str | None = Field(default=None, description="Model id that was probed.")
    llm_budget_remaining_today: int | None = Field(
        default=None,
        description="Model-backed requests left in today's budget; null when the budget is disabled.",
    )


@router.get("/health", response_model=HealthOut)
def health(settings: Settings = Depends(get_settings)) -> HealthOut:
    return HealthOut(
        status="ok",
        product=settings.product_name,
        tagline=settings.tagline,
        version="0.1.0",
        git_sha=os.environ.get("WAGGLE_GIT_SHA", "unknown"),
        built_at=os.environ.get("WAGGLE_BUILT_AT", "unknown"),
    )


@router.get("/ready", response_model=ReadyOut)
def ready(
    request: Request,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ReadyOut:
    db.execute(text("SELECT 1")).scalar_one()
    pgv = db.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).first()
    if pgv is None:
        # Fail-loud: pgvector is a runtime requirement. A /ready endpoint
        # that says "ready" without it would mask a real problem.
        raise RuntimeError(
            "pgvector extension is not installed in this database. "
            "Run `make migrate` (the first migration creates it)."
        )
    probe = get_provider_status(ttl_seconds=settings.llm_probe_ttl_seconds)
    limiter = getattr(request.app.state, "llm_limiter", None)
    return ReadyOut(
        status="ready" if probe.ok else "degraded",
        database="ok",
        pgvector="ok",
        llm=probe.state,
        llm_detail=probe.detail,
        llm_model=probe.model,
        llm_budget_remaining_today=limiter.remaining_today() if limiter else None,
    )
