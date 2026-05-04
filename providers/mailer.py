"""Outbound mailer — JSON records on disk.

Writes each message to `<SENT_MAIL_ROOT>/<timestamp>-<id>.json` so the app
can exercise send-mail flows without hitting an external SMTP / provider.

Public surface:

    def send(to, subject, body_text, body_html=None, attachments=None) -> str

Returns the message id. Side effect: writes one JSON file per send.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENT_MAIL_ROOT = Path("/sent-mail")
SENT_MAIL_ROOT.mkdir(parents=True, exist_ok=True)


def send(
    to: str | list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    **_: Any,
) -> str:
    """Record an outbound email as a JSON file. Returns the generated message id.

    Side effect: writes one JSON file to SENT_MAIL_ROOT.
    """
    msg_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    record = {
        "id": msg_id,
        "sent_at": now.isoformat(),
        "to": [to] if isinstance(to, str) else to,
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": [a.get("filename") for a in (attachments or [])],
    }
    fname = f"{now.strftime('%Y%m%dT%H%M%S')}-{msg_id[:8]}.json"
    (SENT_MAIL_ROOT / fname).write_text(json.dumps(record, indent=2, default=str))
    return msg_id


