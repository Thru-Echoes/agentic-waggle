"""Database layer — SQLModel models, engine factory, and string enums.

Public exports:

    * models — every SQLModel table (Territory, User, Customer, ..., Order)
    * enums  — string enums used across routes / agents / MCP servers
    * session — engine factory + get_db_session context manager

The schema is mirrored 1:1 in `tests/schema.sql`. Alembic generates its
first migration from the metadata here; subsequent migrations should be
authored by hand so we can review the SQL before it runs.
"""
from __future__ import annotations

from db import enums, models, session

__all__ = ["enums", "models", "session"]
