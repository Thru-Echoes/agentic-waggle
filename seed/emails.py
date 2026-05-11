"""80 synthetic inbound emails — bodies + subjects come from the LLM-authored
fixture at `seed/fixtures/emails.json`.

Mix (encoded in the fixture's `_kind` field):
  45 clear orders
  20 ambiguous / questions
   8 non-orders
   7 complex multi-site requests

For each email this module:
  1. Writes the inbox JSON file at `/gmail-fixtures/<account>.inbox.json`
     so `providers/inbox.py` can read it.
  2. Inserts a `synced_emails` row.
  3. Inserts a parallel `documents` row (source_type='email') so the
     email body enters the RAG corpus alongside customer-report
     narratives — the Sales Copilot MCP tool can semantically search
     across both.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import os

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Apiarist, Customer, Email

# Resolution order (matches providers/inbox.py):
#   1. ``INBOX_FIXTURE_ROOT`` env var (used by tests + local dev)
#   2. ``/gmail-fixtures`` (Docker bind-mount)
GMAIL_FIXTURE_ROOT = Path(os.environ.get("INBOX_FIXTURE_ROOT", "/gmail-fixtures"))
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "emails.json"


class FixtureMissing(FileNotFoundError):
    """Raised when seed/fixtures/emails.json is missing."""


def _load_fixture() -> list[dict]:
    if not FIXTURE_PATH.exists():
        raise FixtureMissing(
            f"Email fixture missing at {FIXTURE_PATH}. "
            f"Generate it via `python -m scripts.generate_fixtures`."
        )
    return json.loads(FIXTURE_PATH.read_text())


def _email_address_for_customer_name(name: str) -> str:
    """Mirror the convention used by `scripts.generate_fixtures` so the
    from_address resolves back to the same customer at extraction time."""
    slug = name.lower().replace("'", "").replace(".", "").replace("&", "and")
    slug = "-".join(slug.split())
    return f"contact@{slug}.test"


def _stable_message_id(customer_name: str, body_text: str, index: int) -> str:
    """Derive a deterministic 18-hex-char message id from fixture content.

    Stable across reseeds — the same fixture entry produces the same id
    every run, which keeps the gmail-fixtures/*.inbox.json files
    reproducible and avoids spurious git diffs.
    """
    digest = hashlib.sha256(
        f"{customer_name}|{body_text[:200]}|{index}".encode("utf-8")
    ).hexdigest()
    return digest[:18]


def generate(
    faker: Faker,
    customers: list[Customer],
    apiarists: list[Apiarist],
) -> list[Email]:
    """Build Email models from the fixture.

    The fixture's customer_name + account_id are guaranteed to match the
    deterministic seed via Faker(seed=42). Message ids are content-hashed
    for stability. received_at is randomized within the last 30 days.

    Side effects: filesystem read of the fixture file.
    """
    rows = _load_fixture()
    now = datetime.now(timezone.utc)
    out: list[Email] = []

    for i, r in enumerate(rows):
        msg_id = _stable_message_id(r["customer_name"], r["body_text"], i)
        received = now - timedelta(
            days=faker.random_int(0, 30), hours=faker.random_int(0, 23)
        )
        from_addr = _email_address_for_customer_name(r["customer_name"])
        out.append(
            Email(
                external_id=msg_id,
                thread_id=msg_id,
                account_id=r["account_id"],
                from_address=from_addr,
                to_addresses=[r.get("to_email", r["account_id"])],
                subject=r["subject"],
                body_text=r["body_text"],
                received_at=received,
            )
        )
    return out


def persist(db: Session, emails: list[Email]) -> None:
    """Write inbox fixtures, synced_emails rows, and documents rows.

    Side effects:
      * Filesystem: writes `/gmail-fixtures/<account>.inbox.json`.
      * SQL: INSERTs into `synced_emails` and `documents`.
    """
    # 1. Write the per-account inbox JSON files providers/inbox.py reads.
    GMAIL_FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    by_account: dict[str, list[dict]] = {}
    for e in emails:
        by_account.setdefault(e.account_id, []).append(
            {
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
            }
        )
    for account_id, inbox in by_account.items():
        (GMAIL_FIXTURE_ROOT / f"{account_id}.inbox.json").write_text(
            json.dumps(inbox, indent=2)
        )

    # 2. Insert synced_emails + documents rows.
    for e in emails:
        synced_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO synced_emails "
                "(id, external_id, thread_id, account_id, from_address, "
                " to_addresses, subject, body_text, received_at, is_classified) "
                "VALUES (:id, :ext, :thread, :acct, :from_addr, "
                "        CAST(:to_addrs AS jsonb), :subject, :body, "
                "        :received, false)"
            ),
            {
                "id": synced_id,
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

        # Promote the email into the RAG corpus.
        # Body+subject get embedded; rich metadata enables hybrid retrieval.
        doc_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO documents "
                "(id, source_type, source_id, content, metadata) "
                "VALUES (:id, 'email', :src_id, :content, "
                "        CAST(:meta AS jsonb))"
            ),
            {
                "id": doc_id,
                "src_id": synced_id,
                "content": f"Subject: {e.subject}\n\n{e.body_text}",
                "meta": json.dumps(
                    {
                        "source_type": "email",
                        "external_id": e.external_id,
                        "subject": e.subject,
                        "from_address": e.from_address,
                        "account_id": e.account_id,
                        "received_at": e.received_at.isoformat(),
                    }
                ),
            },
        )
