"""Entry point for synthetic data generation.

Invocation from inside the seed container:

    python -m seed.generate
    python -m seed.generate --drop-existing

Orchestrates generation in FK order. Fail-loud policy throughout:
  * No try/except that swallow persistence errors.
  * Duplicate inserts raise IntegrityError (no ON CONFLICT DO NOTHING).
  * Missing FK references raise LookupError (no silent `continue`).
  * If expected tables do not exist, we raise before attempting to seed
    so the user knows migrations have not been applied.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import logging
import os
import sys
from contextlib import contextmanager
from typing import Iterator

from faker import Faker
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from seed import (
    apiarists as apiarists_gen,
    approval_rules as approval_rules_gen,
    customers as customers_gen,
    emails as emails_gen,
    embeddings as embeddings_gen,
    inspections as inspections_gen,
    orders as orders_gen,
    product_data_sheets as product_data_sheets_gen,
    products as products_gen,
    red_flags as red_flags_gen,
    regions as regions_gen,
    regulated_items as regulated_items_gen,
    regulatory_docs as regulatory_docs_gen,
    services as services_gen,
    sites as sites_gen,
)
from seed.faker_providers import BeekeepingProvider

log = logging.getLogger("seed")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

SEED_VALUE = 42

# Tables we own. Order below is FK-safe for TRUNCATE CASCADE.
OWNED_TABLES_IN_TRUNCATE_ORDER = [
    "order_state_transitions",
    "order_line_items",
    "orders",
    "red_flag_rules",
    "approval_rules",
    "regulated_items",
    "chunks",
    "documents",
    "customer_reports",
    "synced_emails",
    "customer_sites",
    "customers",
    "territory_assignments",
    "territories",
    "services",
    "products",
    "users",
]


class MigrationsNotAppliedError(RuntimeError):
    """Expected tables do not exist. Run `make migrate` first."""


def build_faker() -> Faker:
    faker = Faker(locale="en_US")
    Faker.seed(SEED_VALUE)
    faker.add_provider(BeekeepingProvider)
    return faker


@contextmanager
def session_scope() -> Iterator[Session]:
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://agentic_mcp_crm:agentic_mcp_crm@postgres:5432/agentic_mcp_crm",
    )
    engine = create_engine(db_url, future=True)
    Maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Maker() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def verify_tables_exist(db: Session) -> None:
    """Fail loudly if any expected table is missing."""
    rows = db.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    ).mappings().all()
    existing = {r["table_name"] for r in rows}
    missing = [t for t in OWNED_TABLES_IN_TRUNCATE_ORDER if t not in existing]
    if missing:
        raise MigrationsNotAppliedError(
            f"Expected tables missing: {missing}. "
            f"Run `make migrate` before seeding."
        )


def drop_all_seeded_rows(db: Session) -> None:
    """TRUNCATE every owned table. Errors propagate."""
    verify_tables_exist(db)
    for table in OWNED_TABLES_IN_TRUNCATE_ORDER:
        db.execute(text(f"TRUNCATE {table} CASCADE"))
        log.info("truncated %s", table)


def run(drop_existing: bool) -> None:
    faker = build_faker()
    with session_scope() as db:
        verify_tables_exist(db)

        if drop_existing:
            drop_all_seeded_rows(db)

        log.info("→ generating regions")
        regions = regions_gen.generate()
        regions_gen.persist(db, regions)

        # Order matters for Faker(seed=42) determinism: the test suite +
        # the fixture generator both walk regions → customers → sites →
        # apiarists, so the RNG state at each call lines up across
        # production and CI.
        log.info("→ generating customers (40 with edge-case names)")
        customers = customers_gen.generate(faker)
        customers_gen.persist(db, customers)

        log.info("→ generating customer sites (~75)")
        sites = sites_gen.generate(faker, customers, regions)
        sites_gen.persist(db, sites)

        log.info("→ generating apiarists (12 field + 3 ops + 1 admin)")
        apiarists = apiarists_gen.generate(faker, regions)
        apiarists_gen.persist(db, apiarists)

        log.info("→ generating products (60 SKUs)")
        products = products_gen.generate(faker)
        products_gen.persist(db, products)

        log.info("→ generating services (4 pollination SKUs)")
        services = services_gen.generate()
        services_gen.persist(db, services)

        log.info("→ generating regulated items")
        reg_items = regulated_items_gen.generate(products)
        regulated_items_gen.persist(db, reg_items)

        log.info("→ generating red-flag rules (10)")
        red_flags = red_flags_gen.generate()
        red_flags_gen.persist(db, red_flags)

        log.info("→ generating approval rules (10)")
        approval_rules = approval_rules_gen.generate()
        approval_rules_gen.persist(db, approval_rules)

        log.info("→ generating apiary inspections (40, LLM-authored narratives)")
        inspections = inspections_gen.generate(faker, sites, apiarists)
        inspections_gen.persist(db, inspections)

        log.info("→ generating synthetic emails (80, LLM-authored bodies)")
        emails = emails_gen.generate(faker, customers, apiarists)
        emails_gen.persist(db, emails)

        log.info("→ generating product data sheets (~10)")
        n_sheets = product_data_sheets_gen.persist(db)
        log.info("   wrote %d product data sheets", n_sheets)

        log.info("→ generating regulatory docs (5)")
        n_regs = regulatory_docs_gen.persist(db)
        log.info("   wrote %d regulatory docs", n_regs)

        log.info("→ generating orders (150 across every FSM state)")
        orders = orders_gen.generate(
            faker, customers, sites, apiarists, products, services, emails
        )
        orders_gen.persist(db, orders)

        log.info("→ generating embeddings (sentence-transformers, multi-source)")
        embeddings_gen.run(db)

    log.info("✅ seed complete")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Meridian synthetic data.")
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="TRUNCATE seeded tables before regenerating.",
    )
    args = parser.parse_args(argv)
    run(drop_existing=args.drop_existing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
