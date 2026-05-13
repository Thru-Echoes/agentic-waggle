"""Shared pytest fixtures for the agentic-mcp-crm test suite.

DB tests require TEST_DATABASE_URL to point at a Postgres with pgvector.
If it is not set, DB-backed tests fail loudly rather than silently skip —
the user's requirement is that missing dependencies surface as failures,
not as "passing but hollow" tests. To opt out of DB tests deliberately,
set SKIP_DB_TESTS=1; they will then skip with a clear reason.

Pattern:
    TEST_DATABASE_URL=postgresql://user:pw@localhost:5433/agentic_mcp_crm_test pytest
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest
from faker import Faker
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from seed.faker_providers import BeekeepingProvider

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


# ---------------------------------------------------------------------------
# Faker
# ---------------------------------------------------------------------------

@pytest.fixture
def faker_instance() -> Faker:
    """Deterministic Faker. Each test gets a fresh RNG at seed=42."""
    fk = Faker(locale="en_US")
    Faker.seed(42)
    fk.add_provider(BeekeepingProvider)
    return fk


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _resolve_db_url() -> str:
    if os.environ.get("SKIP_DB_TESTS") == "1":
        pytest.skip(
            "SKIP_DB_TESTS=1 set; DB-backed tests skipped by explicit opt-out",
            allow_module_level=False,
        )
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL not set. DB-backed tests require a real Postgres "
            "with pgvector. Either set the env var (e.g. "
            "postgresql://agentic_mcp_crm:agentic_mcp_crm@localhost:5433/"
            "agentic_mcp_crm_test) or set SKIP_DB_TESTS=1 to deliberately skip.",
            pytrace=False,
        )
    return url


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _resolve_db_url()
    eng = create_engine(url, future=True)
    # Verify connectivity + pgvector up-front so later failures are on the
    # right line.
    with eng.begin() as conn:
        conn.execute(text("SELECT 1"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """Fresh schema per test for full isolation.

    This is slower than a transactional rollback but catches schema-level
    bugs (missing column, bad default, etc.) that transactional tests
    would mask.
    """
    schema_sql = SCHEMA_PATH.read_text()
    with engine.begin() as conn:
        # Split on semicolons at end-of-line to handle the DO / EXTENSION
        # statements correctly.
        for statement in _split_sql(schema_sql):
            conn.execute(text(statement))

    Maker = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = Maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _split_sql(sql: str) -> list[str]:
    """Split a multi-statement SQL file at semicolons.

    Strips `-- …` line comments FIRST, then splits. Stripping before splitting
    matters because a `;` that sits inside a comment line (e.g. `-- foo;
    bar`) would otherwise leak the second half of the comment into the next
    chunk as raw SQL. After stripping, we split and drop any chunk that's
    empty after `.strip()`.

    Limitation: naive wrt semicolons inside string literals or DO blocks.
    Adequate for our schema.sql which uses plain DDL only.
    """
    import re

    no_comments = re.sub(r"--[^\n]*", "", sql)
    return [chunk.strip() for chunk in no_comments.split(";") if chunk.strip()]


# ---------------------------------------------------------------------------
# Filesystem fixtures for providers
# ---------------------------------------------------------------------------

@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "local-storage"
    root.mkdir()
    monkeypatch.setattr("providers.storage.LOCAL_STORAGE_ROOT", root)
    return root


@pytest.fixture
def erp_log_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "erp-sync-log"
    root.mkdir()
    monkeypatch.setattr("providers.erp.ERP_LOG_ROOT", root)
    return root


@pytest.fixture
def inbox_fixture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "gmail-fixtures"
    root.mkdir()
    monkeypatch.setattr("providers.inbox.FIXTURE_ROOT", root)
    return root


@pytest.fixture
def sent_mail_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "sent-mail"
    root.mkdir()
    monkeypatch.setattr("providers.mailer.SENT_MAIL_ROOT", root)
    return root


# ---------------------------------------------------------------------------
# API-key-aware fixtures for the LLM provider
# ---------------------------------------------------------------------------

@pytest.fixture
def require_llm_api_key() -> None:
    """Skip LLM tests only if the user has no API key at all.

    Everywhere else the tests are intentionally strict. For LLM, running
    with no key is the documented failure mode so we test BOTH paths:
      * `test_init_raises_without_api_key` — forces no key
      * network tests marked `require_llm_api_key` — skipped w/ clear
        reason if keys are absent from the environment
    """
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        pytest.skip(
            "No LLM API key set; skipping network-backed LLM test. "
            "This is the documented fail-loud behavior — set ANTHROPIC_API_KEY "
            "to run."
        )


@pytest.fixture
def clean_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip API keys from the environment, so the provider's init raises."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Semantic matcher fixtures (Tasks 3-7)
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session_with_seed(db: Session) -> Session:
    """Provides a SQLAlchemy Session against a fresh schema seeded with
    customers, regions, and sites using the real seed generators.

    Uses `db` (per-test fresh-schema fixture) so test isolation is preserved.
    Calls seed generators directly with the test DB session rather than
    `generate.run()`, which opens its own connection internally.

    Side effects: inserts rows into regions, customers, customer_sites.
    """
    from faker import Faker

    from seed import (
        customers as customers_gen,
        regions as regions_gen,
        sites as sites_gen,
    )
    from seed.faker_providers import BeekeepingProvider

    SEED_VALUE = 42
    faker = Faker(locale="en_US")
    Faker.seed(SEED_VALUE)
    faker.add_provider(BeekeepingProvider)

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)

    customers = customers_gen.generate(faker)
    customers_gen.persist(db, customers)

    sites = sites_gen.generate(faker, customers, regions)
    sites_gen.persist(db, sites)

    db.commit()
    return db


@pytest.fixture
def db_session_with_search_entries(db_session_with_seed: Session) -> Session:
    """Inserts a minimal customer_search_entries set so Stage-A tests can run
    before Task 7's full backfill is implemented.

    Inserts one 'name' entry per customer, one 'site' entry per site, and a
    single known 'alias' entry for O'Reilly Family Orchards ("OReillys Back 40")
    so that site-reference and multi-source tests use a distinctive query with
    no ambiguity.

    Uses the real VectorSearchProvider (all-MiniLM-L6-v2, 384-dim) to build
    embeddings — no mocks.

    This fixture is REPLACED after Task 7 lands and the fixture switches to
    calling backfill_all() directly.

    Side effects: inserts into customer_search_entries; commits.
    """
    from providers.vector_search import VectorSearchProvider

    provider = VectorSearchProvider()
    db = db_session_with_seed

    customer_rows = db.execute(
        text("SELECT id AS customer_id, name AS entry_text FROM customers")
    ).mappings().all()
    for r in customer_rows:
        emb = provider.embed(r["entry_text"])
        db.execute(
            text(
                "INSERT INTO customer_search_entries "
                "(customer_id, entry_text, source, embedding) "
                "VALUES (:cid, :et, 'name', CAST(:emb AS vector))"
            ),
            {"cid": r["customer_id"], "et": r["entry_text"], "emb": emb},
        )

    site_rows = db.execute(
        text("SELECT customer_id, name AS entry_text FROM customer_sites")
    ).mappings().all()
    for r in site_rows:
        emb = provider.embed(r["entry_text"])
        db.execute(
            text(
                "INSERT INTO customer_search_entries "
                "(customer_id, entry_text, source, embedding) "
                "VALUES (:cid, :et, 'site', CAST(:emb AS vector))"
            ),
            {"cid": r["customer_id"], "et": r["entry_text"], "emb": emb},
        )

    # Add a distinctive alias for O'Reilly so site-reference and multi-source
    # tests can use a query with no ambiguity across the 40-customer corpus.
    oreilly_row = db.execute(
        text("SELECT id FROM customers WHERE name LIKE :n"),
        {"n": "%O'Reilly%"},
    ).first()
    if oreilly_row is not None:
        alias_text = "OReillys Back 40"
        emb = provider.embed(alias_text)
        db.execute(
            text(
                "INSERT INTO customer_search_entries "
                "(customer_id, entry_text, source, embedding) "
                "VALUES (:cid, :et, 'alias', CAST(:emb AS vector))"
            ),
            {"cid": oreilly_row.id, "et": alias_text, "emb": emb},
        )

    db.commit()
    return db
