"""80 synthetic emails for the inbox fixture + `synced_emails` table.

Mix:
  45 clear orders (agent should extract successfully)
  20 ambiguous / questions (agent should ask for clarification)
   8 non-orders (newsletters, admin, unrelated)
   7 complex multi-site pollination requests

The 80-email total is sized to cover the 65 orders whose state requires a
`source_email_id` (EXTRACTED 20 + UNDER_REVIEW 18 + APPROVED 15 + SCHEDULED
12 in `seed.orders.STATE_DISTRIBUTION`), with 15 extra for unread-inbox
variety in the demo path.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Apiarist, Customer, Email

GMAIL_FIXTURE_ROOT = Path("/gmail-fixtures")


CLEAR_ORDER_TEMPLATES = [
    "Hi — we need {hives} hives for {crop} bloom starting {date}. Same blocks as last year at {site}. Thanks, {contact}.",
    "{contact} here. Confirming pollination for {site}: {hives} colonies, approx {date}–{date2}. Let me know what paperwork you need.",
    "Quick note — ordering {quantity} of {sku_hint} for delivery to {site}. Invoice to main account.",
    "Ready to schedule pollination for {crop} — about {hives} hives at the {site} block, early {month_name}.",
]

AMBIGUOUS_TEMPLATES = [
    "Do we have capacity for a {crop} job this year? Haven't fully scoped it yet but probably ~{hives} hives.",
    "Thinking about adding queens — Italian or Saskatraz? What's availability look like for spring?",
    "What's the current price on wildflower drums? Might want to place a bulk order.",
    "Is it too late to add hives for my almond block? Bloom is starting.",
]

NON_ORDER_TEMPLATES = [
    "Newsletter: Regional extension news — Varroa pressure update for the quarter.",
    "Reminder: apiary registration renewal due next month.",
    "Just saying hi — great to see you at the conference last week.",
    "Unrelated: can you point me to the updated safety docs?",
]

COMPLEX_TEMPLATES = [
    "We're looking at {hives} hives split across {site}, {site2}, and {site3} — staggered placement over three weeks starting {date}. Need your input on whether that's viable.",
    "Season plan: almonds at {site} ({hives} hives, {date}), cherries at {site2} ({hives2} hives, following month), then blueberries in the summer if we have capacity.",
    "Complicated one: existing contract at {site} for {hives} needs to reduce to {hives2} due to an AFB incident last season. Can we discuss restructuring?",
]


def _email_address_for_customer(customer: Customer) -> str:
    slug = customer.name.lower().replace("'", "").replace(".", "").replace("&", "and")
    slug = "-".join(slug.split())
    return f"contact@{slug}.test"


def generate(
    faker: Faker,
    customers: list[Customer],
    apiarists: list[Apiarist],
) -> list[Email]:
    orchards = [c for c in customers if c.market_segment.startswith("ORCHARD_")]
    out: list[Email] = []
    now = datetime.now(timezone.utc)

    def pick_apiarist_account() -> str:
        apiarist = faker.random_element(apiarists)
        return apiarist.email

    def make_email(template: str, customer: Customer, kind: str, **slots: Any) -> Email:
        body = template.format(**slots) if slots else template
        received = now - timedelta(days=faker.random_int(0, 30), hours=faker.random_int(0, 23))
        msg_id = uuid.uuid4().hex[:18]
        return Email(
            external_id=msg_id,
            thread_id=msg_id,
            account_id=pick_apiarist_account(),
            from_address=_email_address_for_customer(customer),
            to_addresses=[pick_apiarist_account()],
            subject=_subject_for_kind(kind, customer, slots),
            body_text=body,
            received_at=received,
        )

    # Clear orders (45)
    for _ in range(45):
        customer = faker.random_element(orchards or customers)
        template = faker.random_element(CLEAR_ORDER_TEMPLATES)
        hives = faker.random_int(80, 500)
        date1 = (now.date() + timedelta(days=faker.random_int(14, 60))).isoformat()
        date2 = (now.date() + timedelta(days=faker.random_int(70, 90))).isoformat()
        out.append(make_email(
            template, customer, "clear_order",
            hives=hives,
            crop=faker.random_element(["almond", "apple", "blueberry", "cherry"]),
            date=date1,
            date2=date2,
            site=faker.random_element(["North Block 1", "River Block", "Highway Block"]),
            contact=faker.name(),
            quantity=faker.random_int(4, 20),
            sku_hint=faker.random_element(["wildflower drums", "Italian queens", "5-frame nucs"]),
            month_name=faker.random_element(["February", "March", "April", "May"]),
        ))

    # Ambiguous (20)
    for _ in range(20):
        customer = faker.random_element(customers)
        template = faker.random_element(AMBIGUOUS_TEMPLATES)
        out.append(make_email(
            template, customer, "ambiguous",
            hives=faker.random_int(50, 400),
            crop=faker.random_element(["almond", "apple", "blueberry"]),
        ))

    # Non-orders (8)
    for _ in range(8):
        customer = faker.random_element(customers)
        template = faker.random_element(NON_ORDER_TEMPLATES)
        out.append(make_email(template, customer, "non_order"))

    # Complex (7)
    for _ in range(7):
        customer = faker.random_element(orchards or customers)
        template = faker.random_element(COMPLEX_TEMPLATES)
        out.append(make_email(
            template, customer, "complex",
            hives=faker.random_int(200, 800),
            hives2=faker.random_int(100, 400),
            site="North Block", site2="East Block", site3="River Block",
            date=(now.date() + timedelta(days=faker.random_int(30, 60))).isoformat(),
        ))

    return out


def _subject_for_kind(kind: str, customer: Customer, slots: dict[str, Any]) -> str:
    crop = slots.get("crop")
    if kind == "clear_order":
        suffix = f" ({crop})" if crop else ""
        return f"Pollination request — {customer.name}{suffix}"
    if kind == "ambiguous":
        return f"Question from {customer.name}"
    if kind == "non_order":
        return f"From {customer.name}"
    if kind == "complex":
        return f"Multi-site planning — {customer.name}"
    return f"Message from {customer.name}"


def persist(db: Session, emails: list[Email]) -> None:
    # 1. Write inbox fixture files that providers/inbox.py will read
    GMAIL_FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    by_account: dict[str, list[dict]] = {}
    for e in emails:
        by_account.setdefault(e.account_id, []).append({
            "id": e.external_id,
            "thread_id": e.thread_id,
            "account_id": e.account_id,
            "from_address": e.from_address,
            "to_addresses": e.to_addresses,
            "subject": e.subject,
            "body_text": e.body_text,
            "body_html": None,
            "received_at": e.received_at.isoformat(),
            "attachments": [],
        })
    for account_id, inbox in by_account.items():
        (GMAIL_FIXTURE_ROOT / f"{account_id}.inbox.json").write_text(
            json.dumps(inbox, indent=2)
        )

    # 2. Insert SyncedEmail rows so the app sees them even before sync runs.
    #    Duplicate external_id raises IntegrityError — there should be no
    #    duplicate Gmail message ids.
    for e in emails:
        db.execute(
            text(
                "INSERT INTO synced_emails "
                "(id, external_id, thread_id, account_id, from_address, "
                " to_addresses, subject, body_text, received_at, is_classified) "
                "VALUES (:id, :ext, :thread, :acct, :from_addr, "
                "        CAST(:to_addrs AS jsonb), :subject, :body, :received, false)"
            ),
            {
                "id": str(uuid.uuid4()),
                "ext": e.external_id,
                "thread": e.thread_id,
                "acct": e.account_id,
                "from_addr": e.from_address,
                "to_addrs": json.dumps(e.to_addresses),
                "subject": e.subject,
                "body": e.body_text,
                "received": e.received_at,
            },
        )
