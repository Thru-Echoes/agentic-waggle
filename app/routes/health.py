"""Health + readiness routes.

`/health` is a liveness probe (process is up); `/ready` checks the DB is
reachable and pgvector is installed. Both return JSON.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlmodel import Session

from app.config import Settings, get_settings
from db.session import get_db_session

router = APIRouter(tags=["health"])


class HealthOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    product: str
    tagline: str


class ReadyOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    database: str
    pgvector: str


@router.get("/health", response_model=HealthOut)
def health(settings: Settings = Depends(get_settings)) -> HealthOut:
    return HealthOut(
        status="ok",
        product=settings.product_name,
        tagline=settings.tagline,
    )


@router.get("/ready", response_model=ReadyOut)
def ready(db: Session = Depends(get_db_session)) -> ReadyOut:
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
    return ReadyOut(status="ready", database="ok", pgvector="ok")
