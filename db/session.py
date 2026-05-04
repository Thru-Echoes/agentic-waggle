"""Engine factory + session scope for Waggle.

The FastAPI dependency `get_db_session` yields a short-lived SQLAlchemy
Session scoped to one request, committing on success and rolling back on
exception. The engine is a module-level singleton, constructed lazily on
first use so test-suite imports don't fail when DATABASE_URL is unset.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = (
    "postgresql://agentic_mcp_crm:agentic_mcp_crm@postgres:5432/agentic_mcp_crm"
)


def get_database_url() -> str:
    """Return the current DATABASE_URL or the compose default. Not cached."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the singleton engine. Side effects: opens a pool on first call."""
    url = get_database_url()
    log.info("creating db engine: %s", url.split("@", 1)[-1])  # don't log creds
    return create_engine(url, future=True, pool_pre_ping=True)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session. Commits on success, rolls back on exception.

    Side effects: opens a DB connection, writes on commit.
    """
    maker = _session_factory()
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session() -> Iterator[Session]:
    """FastAPI dependency — yields a request-scoped session."""
    with session_scope() as session:
        yield session
