"""Container entrypoint that prepares the DB then hands off to uvicorn.

Runs in two blocking phases, then hands off:

1. `alembic upgrade head` — apply migrations idempotently. No-op if the DB
   is already at the head revision. MUST precede serving.
2. Seed if (and only if) the `customers` table is empty. The seed pipeline
   itself is fail-loud: re-running on a populated DB raises IntegrityError,
   which is the right behavior for local dev but the wrong behavior for an
   auto-restart in production. The empty-DB guard here is what makes the
   container restart-safe.

The matcher search-index backfill is deliberately NOT run here. It is a
slow synchronous embed pass; running it before `exec uvicorn` left Fly's
health check hitting an unbound port (the "not listening on
0.0.0.0:5001" fragility). Instead `main()` sets `WAGGLE_BOOT_REINDEX=1`
and the app's lifespan (app.main._run_boot_reindex) runs `reindex_if_empty`
as a POST-BIND background task — the port binds immediately; matcher
calls raise the loud, expected `MatcherIndexEmpty` during the brief
rebuild window.

After preflight, exec into uvicorn so signals propagate cleanly.

Side effects:
    * runs the alembic CLI
    * may execute the full `seed.generate` pipeline (writes ~17 tables of rows)
    * sets WAGGLE_BOOT_REINDEX in the environment uvicorn inherits
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


def reindex_if_empty(db) -> None:
    """Boot step: rebuild search indexes if either table is empty.

    Runs after alembic upgrade head and before uvicorn exec. Idempotent —
    skips work when both tables already have rows. Required so first
    deploys after migration 004 don't hit MatcherIndexEmpty on the
    first matcher call.

    Side effects:
        * Reads counts from customer_search_entries + sku_search_entries.
        * If either is empty, runs providers.semantic_matcher.backfill_all
          which TRUNCATEs both tables and rebuilds them.
        * Logs at INFO.

    Parameters:
        db: SQLAlchemy Session for DB reads and writes.
    """
    from providers import semantic_matcher

    cust_count = db.execute(
        text("SELECT COUNT(*) FROM customer_search_entries")
    ).scalar_one()
    sku_count = db.execute(
        text("SELECT COUNT(*) FROM sku_search_entries")
    ).scalar_one()
    if cust_count and sku_count:
        log.info("boot: matcher indexes already populated; skipping reindex")
        return
    log.info(
        "boot: reindexing matcher (customer=%d, sku=%d)", cust_count, sku_count
    )
    n = semantic_matcher.backfill_all(db)
    log.info("boot: matcher reindex complete (%d entries)", n)


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
    # Matcher reindex is deferred to the app's POST-BIND lifespan task
    # (app.main._run_boot_reindex) so a cold deploy binds 0.0.0.0:$PORT
    # immediately instead of blocking Fly's health check on the
    # synchronous one-by-one embed pass. reindex_if_empty() below is the
    # shared implementation that task calls.
    os.environ["WAGGLE_BOOT_REINDEX"] = "1"
    exec_uvicorn()


if __name__ == "__main__":
    sys.exit(main())
