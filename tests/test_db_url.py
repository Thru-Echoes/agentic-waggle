"""Unit tests for ``db.url.get_sqlalchemy_url`` (no database required).

The contract under test: every engine in the project is built from a URL
that names the psycopg2 driver, regardless of how the operator spelled
``DATABASE_URL``. A bare ``postgresql://`` would make SQLAlchemy 2.1 look
for psycopg v3, which is not a dependency.
"""
from __future__ import annotations

import pytest

from db.url import DEFAULT_DATABASE_URL, get_sqlalchemy_url


def test_bare_postgresql_scheme_gets_psycopg2_driver() -> None:
    url = get_sqlalchemy_url("postgresql://u:p@host:5432/db")
    assert url == "postgresql+psycopg2://u:p@host:5432/db"


def test_heroku_style_postgres_scheme_gets_psycopg2_driver() -> None:
    url = get_sqlalchemy_url("postgres://u:p@host/db")
    assert url == "postgresql+psycopg2://u:p@host/db"


def test_explicit_driver_is_left_untouched() -> None:
    explicit = "postgresql+psycopg2://u:p@host/db"
    assert get_sqlalchemy_url(explicit) == explicit
    other_driver = "postgresql+psycopg://u:p@host/db"
    assert get_sqlalchemy_url(other_driver) == other_driver


def test_non_postgres_url_raises_loudly() -> None:
    with pytest.raises(ValueError, match="must be a PostgreSQL URL"):
        get_sqlalchemy_url("sqlite:///waggle.db")


def test_env_var_is_read_when_no_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://env:env@envhost/envdb")
    assert get_sqlalchemy_url() == "postgresql+psycopg2://env:env@envhost/envdb"


def test_default_is_used_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_sqlalchemy_url() == DEFAULT_DATABASE_URL
    assert DEFAULT_DATABASE_URL.startswith("postgresql+psycopg2://")
