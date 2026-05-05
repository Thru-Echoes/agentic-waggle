"""Container entrypoint that prepares the DB then hands off to uvicorn.

Runs in two phases:

1. `alembic upgrade head` — apply migrations idempotently. No-op if the DB
   is already at the head revision.
2. Seed if (and only if) the `customers` table is empty. The seed pipeline
   itself is fail-loud: re-running on a populated DB raises IntegrityError,
   which is the right behavior for local dev but the wrong behavior for an
   auto-restart in production. The empty-DB guard here is what makes the
   container restart-safe.

After preflight, exec into uvicorn so signals propagate cleanly.

Side effects:
    * runs the alembic CLI
    * may execute the full `seed.generate` pipeline (writes ~17 tables of rows)
    * replaces the current process with uvicorn (no return)

Reads `DATABASE_URL` and uvicorn host/port from `app.config.Settings`.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import os
import subprocess
import sys

from sqlalchemy import create_engine, text

log = logging.getLogger("boot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def run_migrations() -> None:
    """Apply alembic migrations to head. Side effect: modifies DB schema."""
    log.info("running alembic upgrade head")
    subprocess.run(["alembic", "upgrade", "head"], check=True)


def seed_if_empty() -> None:
    """Run the seed pipeline only if `customers` is empty.

    Avoids the fail-loud duplicate-key error that re-running seed on a
    populated DB would raise. Side effects: SELECT against `customers`,
    and (on first boot only) the full seed pipeline writes ~17 tables.
    """
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set; cannot check seed state")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar_one()
    if count and int(count) > 0:
        log.info("DB already seeded (%s customers); skipping seed", count)
        return
    log.info("DB empty; running seed.generate")
    from seed.generate import run as seed_run

    seed_run(drop_existing=False)


def exec_uvicorn() -> None:
    """Replace this process with uvicorn. Side effect: never returns on success."""
    from app.config import get_settings

    settings = get_settings()
    cmd = [
        "uvicorn",
        "app.main:app",
        "--host",
        settings.host,
        "--port",
        str(settings.port),
    ]
    log.info("exec uvicorn: %s", " ".join(cmd))
    os.execvp(cmd[0], cmd)


def main() -> None:
    run_migrations()
    seed_if_empty()
    exec_uvicorn()


if __name__ == "__main__":
    sys.exit(main())
