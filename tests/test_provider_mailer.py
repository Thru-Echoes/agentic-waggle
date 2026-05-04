"""E2E tests for the mailer provider."""
# pyright: reportMissingImports=false
from __future__ import annotations

import json

from providers.mailer import send


class TestSend:
    def test_writes_json_file(self, sent_mail_root) -> None:
        msg_id = send(
            to="customer@example.test",
            subject="Pollination quote",
            body_text="400 hives available for Feb bloom.",
        )
        assert msg_id
        files = list(sent_mail_root.glob("*.json"))
        assert len(files) == 1
        record = json.loads(files[0].read_text())
        assert record["id"] == msg_id
        assert record["to"] == ["customer@example.test"]
        assert record["subject"] == "Pollination quote"

    def test_multiple_recipients_preserved(self, sent_mail_root) -> None:
        send(
            to=["a@test", "b@test"],
            subject="s",
            body_text="b",
        )
        files = list(sent_mail_root.glob("*.json"))
        record = json.loads(files[0].read_text())
        assert record["to"] == ["a@test", "b@test"]

    def test_attachments_recorded_by_filename(self, sent_mail_root) -> None:
        send(
            to="x@test",
            subject="s",
            body_text="b",
            attachments=[{"filename": "quote.pdf", "content": b"..."}],
        )
        files = list(sent_mail_root.glob("*.json"))
        record = json.loads(files[0].read_text())
        assert record["attachments"] == ["quote.pdf"]


