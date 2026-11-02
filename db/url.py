"""Database URL normalization.

SQLAlchemy 2.1 changed the default driver for ``postgresql://`` URLs from
psycopg2 to psycopg (v3). Waggle ships psycopg2 only, so every engine must be
built from a URL that names the driver explicitly. ``get_sqlalchemy_url``
upgrades a bare ``postgresql://`` (or Heroku-style ``postgres://``) URL to
``postgresql+psycopg2://`` and leaves a URL that already names a driver
untouched, so an operator-supplied ``DATABASE_URL`` keeps working across
SQLAlchemy versions.

Public surface:

    DEFAULT_DATABASE_URL: str
    get_sqlalchemy_url(url: str | None = None) -> str

Side effects: ``get_sqlalchemy_url`` reads ``DATABASE_URL`` from the
environment when no explicit URL is passed. Nothing else.
"""
from __future__ import annotations

import os

#: Compose-network default (service ``postgres`` on the compose network).
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg2://agentic_mcp_crm:agentic_mcp_crm@postgres:5432/agentic_mcp_crm"
)

_DRIVER_PREFIX = "postgresql+psycopg2://"
_BARE_PREFIXES = ("postgresql://", "postgres://")


def get_sqlalchemy_url(url: str | None = None) -> str:
    """Return a PostgreSQL SQLAlchemy URL that names the psycopg2 driver.

    Resolution order: the ``url`` argument, then the ``DATABASE_URL``
    environment variable, then ``DEFAULT_DATABASE_URL``.

    Parameters:
        url: An explicit URL. ``None`` means "resolve from the environment".

    Returns:
        The same URL with a ``postgresql+psycopg2://`` scheme.

    Raises:
        ValueError: if the URL is not a PostgreSQL URL. Fail-loud: a wrong
            scheme here would otherwise surface as an opaque dialect import
            error at first connection.

    Side effects: reads ``DATABASE_URL`` when ``url`` is ``None``.
    """
    raw = url if url is not None else os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    for bare in _BARE_PREFIXES:
        if raw.startswith(bare):
            return _DRIVER_PREFIX + raw[len(bare):]
    if raw.startswith("postgresql+"):
        return raw
    scheme = raw.split("://", 1)[0] if "://" in raw else raw[:24]
    raise ValueError(
        f"DATABASE_URL must be a PostgreSQL URL (postgresql://… or "
        f"postgresql+psycopg2://…); got scheme {scheme!r}. "
        "Check the DATABASE_URL environment variable."
    )


__all__ = ["DEFAULT_DATABASE_URL", "get_sqlalchemy_url"]
