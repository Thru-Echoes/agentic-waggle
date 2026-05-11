"""Inbox provider — fixture-backed inbound mail reader.

Reads from `<FIXTURE_ROOT>/<account_id>.inbox.json` and tracks read state in
`<account_id>.read.json` alongside it. The seed generator writes the inbox
fixtures; tests can provide their own.

Policy: missing fixtures are hard errors. A missing inbox fixture means the
seed did not write for that account, which is a bug — it must not silently
look like "there's no mail". Call sites that genuinely expect an empty inbox
should catch `InboxFixtureMissing` explicitly.

Public surface:

    @dataclass
    class EmailMessage:
        id, thread_id, account_id, from_address, to_addresses, subject,
        body_text, body_html, received_at, attachments

    class InboxClient:
        def authenticate(account_id: str) -> None
        def list_unread(account_id: str, since: datetime | None) -> list[EmailMessage]
        def fetch(account_id: str, message_id: str) -> EmailMessage
        def mark_read(account_id: str, message_id: str) -> None

Side effects: touches marker files on authenticate; writes `<account>.read.json`
on mark_read.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Resolution order:
#   1. ``INBOX_FIXTURE_ROOT`` env var (used by tests + local dev outside Docker)
#   2. ``/gmail-fixtures`` (the bind-mount path inside the Docker container)
#
# Resolved at import time. Tests that want to override per-test can still
# ``monkeypatch.setattr("providers.inbox.FIXTURE_ROOT", tmp_path)``.
FIXTURE_ROOT = Path(os.environ.get("INBOX_FIXTURE_ROOT", "/gmail-fixtures"))


@dataclass
class EmailMessage:
    id: str
    thread_id: str
    account_id: str
    from_address: str
    to_addresses: list[str]
    subject: str
    body_text: str
    body_html: str | None
    received_at: datetime
    attachments: list[dict[str, Any]]


class InboxFixtureMissing(FileNotFoundError):
    """Raised when an inbox fixture file is not found.

    Treated as a real error rather than "no mail" because silent empty-list
    behavior hides seeding bugs.
    """


def _inbox_path(account_id: str) -> Path:
    return FIXTURE_ROOT / f"{account_id}.inbox.json"


def _read_state_path(account_id: str) -> Path:
    return FIXTURE_ROOT / f"{account_id}.read.json"


def _load_inbox(account_id: str) -> list[dict[str, Any]]:
    """Return the list of message dicts for an account. Raises if the fixture is missing."""
    path = _inbox_path(account_id)
    if not path.exists():
        raise InboxFixtureMissing(
            f"No inbox fixture at {path}. "
            f"Seed did not write for account_id={account_id!r}. "
            f"Run `make seed` or `make reseed` to regenerate fixtures."
        )
    return json.loads(path.read_text())


def _load_read(account_id: str) -> set[str]:
    """Return the set of message ids marked read. Empty set if no state file exists."""
    p = _read_state_path(account_id)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text()))


def _save_read(account_id: str, read: set[str]) -> None:
    """Persist the read-id set. Side effect: filesystem write."""
    _read_state_path(account_id).write_text(json.dumps(sorted(read)))


def _row_to_msg(row: dict[str, Any]) -> EmailMessage:
    return EmailMessage(
        id=row["id"],
        thread_id=row.get("thread_id", row["id"]),
        account_id=row["account_id"],
        from_address=row["from_address"],
        to_addresses=row.get("to_addresses", []),
        subject=row.get("subject", ""),
        body_text=row.get("body_text", ""),
        body_html=row.get("body_html"),
        received_at=datetime.fromisoformat(row["received_at"]),
        attachments=row.get("attachments", []),
    )


class InboxClient:
    """Fixture-backed inbox. Reads synthetic emails from disk.

    Raises FileNotFoundError at construction if FIXTURE_ROOT does not exist.
    """

    def __init__(self, **_: Any) -> None:
        if not FIXTURE_ROOT.exists():
            raise FileNotFoundError(
                f"Inbox fixtures root {FIXTURE_ROOT} does not exist. "
                f"Mount it in docker-compose.yml before starting the app."
            )

    def authenticate(self, account_id: str) -> None:
        """Touch a marker file so tests and telemetry can observe the call."""
        (FIXTURE_ROOT / f"{account_id}.authed").touch()

    def list_unread(
        self,
        account_id: str,
        since: datetime | None = None,
    ) -> list[EmailMessage]:
        """Return messages not yet marked read, newest first per fixture order."""
        inbox = _load_inbox(account_id)
        read = _load_read(account_id)
        out: list[EmailMessage] = []
        for row in inbox:
            if row["id"] in read:
                continue
            if since is not None:
                if datetime.fromisoformat(row["received_at"]) < since:
                    continue
            out.append(_row_to_msg(row))
        return out

    def fetch(self, account_id: str, message_id: str) -> EmailMessage:
        """Return the full message by id. Raises LookupError if not found."""
        for row in _load_inbox(account_id):
            if row["id"] == message_id:
                return _row_to_msg(row)
        raise LookupError(
            f"No message {message_id!r} for account {account_id!r} "
            f"in fixture {_inbox_path(account_id)}"
        )

    def mark_read(self, account_id: str, message_id: str) -> None:
        """Mark a message id as read. Verifies the id exists first."""
        _ = self.fetch(account_id, message_id)
        read = _load_read(account_id)
        read.add(message_id)
        _save_read(account_id, read)
