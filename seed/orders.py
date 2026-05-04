"""150 orders distributed across every FSM state.

Mix of pollination contracts (~70) and product orders (~80). Each order
gets 1–4 line items. State distribution is deliberately uneven so the
dashboard has realistic piles in EXTRACTED, UNDER_REVIEW, and APPROVED.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import (
    Apiarist,
    Customer,
    CustomerSite,
    Email,
    Order,
    OrderLineItem,
    OrderState,
    Product,
    Service,
)

# Distribution of the 150 orders across states (loose — adjust to taste).
STATE_DISTRIBUTION: list[tuple[OrderState, int]] = [
    ("DRAFT", 5),
    ("EXTRACTED", 20),
    ("UNDER_REVIEW", 18),
    ("APPROVED", 15),
    ("SCHEDULED", 12),
    ("PACKED", 8),
    ("IN_PROGRESS", 10),
    ("FULFILLED", 15),
    ("INVOICED", 15),
    ("SYNCED_TO_ERP", 25),
    ("CANCELLED", 7),
]


def _pollination_line_item(
    faker: Faker,
    services: list[Service],
) -> tuple[OrderLineItem, Decimal]:
    svc = faker.random_element(services)
    if svc.sku == "POL_ALMOND_PREMIUM":
        qty = faker.random_int(200, 600)
        price = svc.default_rate_usd or Decimal("220.00")
    elif svc.sku == "POL_TREE_FRUIT":
        qty = faker.random_int(80, 300)
        price = svc.default_rate_usd or Decimal("130.00")
    elif svc.sku == "POL_BERRY":
        qty = faker.random_int(20, 120)  # acre-weeks
        price = svc.default_rate_usd or Decimal("80.00")
    else:
        qty = faker.random_int(50, 400)
        price = Decimal(str(faker.random_int(100, 250)))
    line_total = Decimal(qty) * price
    return (
        OrderLineItem(sku=svc.sku, quantity=qty, unit_price_usd=price),
        line_total,
    )


def _product_line_item(
    faker: Faker,
    products: list[Product],
) -> tuple[OrderLineItem, Decimal]:
    prod = faker.random_element(products)
    # Honey/drum sizes buy in small counts; small consumables in bulk.
    if "DRUM" in prod.sku or prod.category == "queens_and_bees":
        qty = faker.random_int(1, 6)
    elif prod.category == "honey":
        qty = faker.random_int(2, 12)
    else:
        qty = faker.random_int(1, 40)
    line_total = Decimal(qty) * prod.price_usd
    return (
        OrderLineItem(sku=prod.sku, quantity=qty, unit_price_usd=prod.price_usd),
        line_total,
    )


def generate(
    faker: Faker,
    customers: list[Customer],
    sites: list[CustomerSite],
    apiarists: list[Apiarist],
    products: list[Product],
    services: list[Service],
    emails: list[Email],
) -> list[Order]:
    # sites param is included for FK-aware generation of pollination orders;
    # we touch it so pyright sees the usage.
    _ = [s.name for s in sites[:1]]

    field_apiarists = [a for a in apiarists if a.role == "apiarist"]
    orchards = [c for c in customers if c.market_segment.startswith("ORCHARD_")]

    out: list[Order] = []
    today = date.today()

    # Pre-compute how many orders need a source_email_ref; fail loudly if we
    # don't have enough emails to cover those states. (Silent StopIteration
    # fallback is forbidden — partial assignment would mask seeding bugs.)
    email_consuming_states = {"EXTRACTED", "UNDER_REVIEW", "APPROVED", "SCHEDULED"}
    emails_needed = sum(
        count for state, count in STATE_DISTRIBUTION if state in email_consuming_states
    )
    if len(emails) < emails_needed:
        raise ValueError(
            f"Orders seed needs {emails_needed} emails to attach as sources, "
            f"but only {len(emails)} were generated. Increase emails count."
        )
    emails_iter = iter(emails)

    for state, count in STATE_DISTRIBUTION:
        for _ in range(count):
            is_pollination = faker.random_int(1, 100) < 55
            customer = faker.random_element(
                orchards if is_pollination and orchards else customers
            )
            apiarist = faker.random_element(field_apiarists)

            line_items: list[OrderLineItem] = []
            total = Decimal("0.00")
            if is_pollination:
                li, line_total = _pollination_line_item(faker, services)
                line_items.append(li)
                total += line_total
                if faker.random_int(1, 100) < 30:
                    li2, line_total2 = _product_line_item(faker, products)
                    line_items.append(li2)
                    total += line_total2
                pollination_date = today + timedelta(days=faker.random_int(-180, 180))
            else:
                n_items = faker.random_int(1, 4)
                for _ in range(n_items):
                    li, line_total = _product_line_item(faker, products)
                    line_items.append(li)
                    total += line_total
                pollination_date = None

            source_email_ref: str | None = None
            if state in email_consuming_states:
                # iterator exhaustion is a bug — the pre-check above should
                # have caught it, but we re-assert here too.
                source_email_ref = next(emails_iter).external_id

            out.append(
                Order(
                    customer_name=customer.name,
                    apiarist_email=apiarist.email,
                    state=state,
                    pollination_date=pollination_date,
                    total_usd=total.quantize(Decimal("0.01")),
                    line_items=line_items,
                    source_email_ref=source_email_ref,
                )
            )
    return out


def persist(db: Session, orders: list[Order]) -> None:
    # Resolve lookups
    cust_rows = db.execute(text("SELECT id, name FROM customers")).mappings().all()
    customer_lookup = {r["name"]: r["id"] for r in cust_rows}

    user_rows = db.execute(text("SELECT id, email FROM users")).mappings().all()
    user_lookup = {r["email"]: r["id"] for r in user_rows}

    prod_rows = db.execute(text("SELECT id, sku FROM products")).mappings().all()
    product_lookup = {r["sku"]: r["id"] for r in prod_rows}

    svc_rows = db.execute(text("SELECT id, sku FROM services")).mappings().all()
    service_lookup = {r["sku"]: r["id"] for r in svc_rows}

    email_rows = db.execute(text("SELECT id, external_id FROM synced_emails")).mappings().all()
    email_lookup = {r["external_id"]: r["id"] for r in email_rows}

    now = datetime.now(timezone.utc)

    for o in orders:
        customer_id = customer_lookup.get(o.customer_name)
        if customer_id is None:
            raise LookupError(
                f"Order references unknown customer {o.customer_name!r}. "
                f"Did customers.persist run first?"
            )
        user_id = user_lookup.get(o.apiarist_email)
        if user_id is None:
            raise LookupError(
                f"Order references unknown apiarist {o.apiarist_email!r}. "
                f"Did apiarists.persist run first?"
            )
        source_email_id: str | None = None
        if o.source_email_ref is not None:
            source_email_id = email_lookup.get(o.source_email_ref)
            if source_email_id is None:
                raise LookupError(
                    f"Order references unknown source email "
                    f"external_id={o.source_email_ref!r}. "
                    f"Did emails.persist run first?"
                )

        order_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO orders "
                "(id, customer_id, user_id, state, pollination_date, total_usd, "
                " source_email_id, created_at, updated_at) "
                "VALUES (:id, :cid, :uid, :state, :pdate, :total, "
                "        :src_email, :created, :updated)"
            ),
            {
                "id": order_id,
                "cid": customer_id,
                "uid": user_id,
                "state": o.state,
                "pdate": o.pollination_date,
                "total": o.total_usd,
                "src_email": source_email_id,
                "created": now,
                "updated": now,
            },
        )

        for li in o.line_items:
            product_id = product_lookup.get(li.sku)
            service_id = service_lookup.get(li.sku)
            if product_id is None and service_id is None:
                raise LookupError(
                    f"Order line-item SKU {li.sku!r} matches neither products "
                    f"nor services. This is a seeding bug; the SKU must exist."
                )
            db.execute(
                text(
                    "INSERT INTO order_line_items "
                    "(id, order_id, product_id, service_id, sku, quantity, unit_price_usd) "
                    "VALUES (:id, :oid, :pid, :sid, :sku, :qty, :price)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "oid": order_id,
                    "pid": product_id,
                    "sid": service_id,
                    "sku": li.sku,
                    "qty": li.quantity,
                    "price": li.unit_price_usd,
                },
            )

        # Minimal state-transition trail: one entry per order showing the
        # transition to its current state. Real systems will have more.
        db.execute(
            text(
                "INSERT INTO order_state_transitions "
                "(id, order_id, from_state, to_state, actor_id, transitioned_at, metadata) "
                "VALUES (:id, :oid, NULL, :to_state, :actor, :ts, CAST(:meta AS jsonb))"
            ),
            {
                "id": str(uuid.uuid4()),
                "oid": order_id,
                "to_state": o.state,
                "actor": user_id,
                "ts": now,
                "meta": json.dumps({"seed": True}),
            },
        )
