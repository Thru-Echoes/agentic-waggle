"""Start a real uvicorn server for browser-driven tests.

``start_live_server`` seeds a minimal dataset into the test database (fresh
schema from ``tests/schema.sql``), copies the committed inbox fixtures to a
scratch directory so the seed never rewrites them, and launches
``app.main:app`` in a subprocess with the given environment overrides.

Side effects: recreates the test schema, writes fixture copies under the
given scratch directory, spawns and later terminates a uvicorn process.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from db.url import get_sqlalchemy_url

REPO_ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    """Bind to port 0, return the OS-assigned free port, then release it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for_server(host: str, port: int, timeout: float = 30.0) -> None:
    """Poll a TCP connect until the server accepts or ``timeout`` elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"server at {host}:{port} did not start within {timeout}s")


def _split_sql(sql: str) -> list[str]:
    import re

    no_comments = re.sub(r"--[^\n]*", "", sql)
    return [c.strip() for c in no_comments.split(";") if c.strip()]


def seed_minimal(db_url: str) -> None:
    """Fresh schema + regions, customers, sites, apiarists, products, services,
    and the matcher search index. Enough for /inbox, /customers and an
    Extract attempt. Side effects: drops and recreates every table."""
    from faker import Faker
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from providers.semantic_matcher import backfill_all
    from seed import apiarists as apiarists_gen
    from seed import customers as customers_gen
    from seed import products as products_gen
    from seed import regions as regions_gen
    from seed import services as services_gen
    from seed import sites as sites_gen
    from seed.faker_providers import BeekeepingProvider

    fk = Faker(locale="en_US")
    Faker.seed(42)
    fk.add_provider(BeekeepingProvider)

    engine = create_engine(get_sqlalchemy_url(db_url), future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        for stmt in _split_sql((REPO_ROOT / "tests" / "schema.sql").read_text()):
            conn.execute(text(stmt))
    maker = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = maker()
    try:
        regions = regions_gen.generate()
        regions_gen.persist(session, regions)
        customers = customers_gen.generate(fk)
        customers_gen.persist(session, customers)
        sites_gen.persist(session, sites_gen.generate(fk, customers, regions))
        apiarists_gen.persist(session, apiarists_gen.generate(fk, regions))
        products_gen.persist(session, products_gen.generate(fk))
        services_gen.persist(session, services_gen.generate())
        backfill_all(session)
        session.commit()
    finally:
        session.close()
        engine.dispose()


@dataclass(frozen=True)
class LiveServer:
    """A running uvicorn subprocess and its base URL."""

    base_url: str
    process: subprocess.Popen[bytes]

    def stop(self) -> None:
        """Terminate the subprocess (kill after 5 s). Side effect: process exit."""
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def start_live_server(scratch: Path, *, env_overrides: dict[str, str] | None = None,
                      drop_env: tuple[str, ...] = ()) -> LiveServer:
    """Seed the test DB and launch uvicorn with the given environment.

    ``scratch`` receives a copy of ``gmail-fixtures`` (the server reads and
    the seed may write there) and the provider roots. Keys listed in
    ``drop_env`` are removed from the child environment.

    Side effects: see module docstring.
    """
    db_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://agentic_mcp_crm:agentic_mcp_crm@localhost:5433/agentic_mcp_crm_test",
    )
    seed_minimal(db_url)

    fixtures = scratch / "gmail-fixtures"
    if not fixtures.exists():
        shutil.copytree(REPO_ROOT / "gmail-fixtures", fixtures)
    for sub in ("erp-sync-log", "local-storage", "sent-mail"):
        (scratch / sub).mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    for key in drop_env:
        env.pop(key, None)
    env.update({
        "DATABASE_URL": db_url,
        "INBOX_FIXTURE_ROOT": str(fixtures),
        "ERP_LOG_ROOT": str(scratch / "erp-sync-log"),
        "STORAGE_ROOT": str(scratch / "local-storage"),
        "SENT_MAIL_ROOT": str(scratch / "sent-mail"),
    })
    env.update(env_overrides or {})

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wait_for_server("127.0.0.1", port)
    return LiveServer(base_url=f"http://127.0.0.1:{port}", process=proc)


__all__ = ["LiveServer", "REPO_ROOT", "free_port", "seed_minimal", "start_live_server", "wait_for_server"]
