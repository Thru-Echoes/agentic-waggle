"""E2E tests for the inbox provider."""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from providers.inbox import InboxClient, InboxFixtureMissing


def _write_fixture(root, account: str, messages: list[dict]) -> None:
    (root / f"{account}.inbox.json").write_text(json.dumps(messages))


def _sample_message(id_: str, account: str, received_at: str) -> dict:
    return {
        "id": id_,
        "thread_id": id_,
        "account_id": account,
        "from_address": "sender@example.test",
        "to_addresses": [account],
        "subject": "Test",
        "body_text": "body",
        "body_html": None,
        "received_at": received_at,
        "attachments": [],
    }


class TestListUnread:
    def test_missing_fixture_file_raises_loudly(self, inbox_fixture_root) -> None:
        c = InboxClient()
        with pytest.raises(InboxFixtureMissing):
            c.list_unread("nonexistent@example.test")

    def test_returns_all_messages_when_none_read(self, inbox_fixture_root) -> None:
        acct = "rep@example.test"
        _write_fixture(inbox_fixture_root, acct, [
            _sample_message("m1", acct, "2026-01-01T00:00:00+00:00"),
            _sample_message("m2", acct, "2026-01-02T00:00:00+00:00"),
        ])
        c = InboxClient()
        msgs = c.list_unread(acct)
        assert len(msgs) == 2

    def test_excludes_messages_marked_read(self, inbox_fixture_root) -> None:
        acct = "rep@example.test"
        _write_fixture(inbox_fixture_root, acct, [
            _sample_message("m1", acct, "2026-01-01T00:00:00+00:00"),
            _sample_message("m2", acct, "2026-01-02T00:00:00+00:00"),
        ])
        c = InboxClient()
        c.mark_read(acct, "m1")
        msgs = c.list_unread(acct)
        assert [m.id for m in msgs] == ["m2"]

    def test_respects_since_filter(self, inbox_fixture_root) -> None:
        acct = "rep@example.test"
        _write_fixture(inbox_fixture_root, acct, [
            _sample_message("old", acct, "2020-01-01T00:00:00+00:00"),
            _sample_message("new", acct, "2026-01-01T00:00:00+00:00"),
        ])
        c = InboxClient()
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        msgs = c.list_unread(acct, since=cutoff)
        assert [m.id for m in msgs] == ["new"]


class TestFetchMarkRead:
    def test_fetch_returns_specific_message(self, inbox_fixture_root) -> None:
        acct = "rep@example.test"
        _write_fixture(inbox_fixture_root, acct, [
            _sample_message("m1", acct, "2026-01-01T00:00:00+00:00"),
        ])
        c = InboxClient()
        msg = c.fetch(acct, "m1")
        assert msg.id == "m1"

    def test_fetch_unknown_id_raises(self, inbox_fixture_root) -> None:
        acct = "rep@example.test"
        _write_fixture(inbox_fixture_root, acct, [
            _sample_message("m1", acct, "2026-01-01T00:00:00+00:00"),
        ])
        c = InboxClient()
        with pytest.raises(LookupError):
            c.fetch(acct, "no-such-id")

    def test_mark_read_verifies_message_exists(self, inbox_fixture_root) -> None:
        acct = "rep@example.test"
        _write_fixture(inbox_fixture_root, acct, [])
        c = InboxClient()
        with pytest.raises(LookupError):
            c.mark_read(acct, "made-up-id")


class TestAuthenticate:
    def test_writes_marker_file(self, inbox_fixture_root) -> None:
        c = InboxClient()
        c.authenticate("rep@example.test")
        assert (inbox_fixture_root / "rep@example.test.authed").exists()


class TestInit:
    def test_missing_fixture_root_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "providers.inbox.FIXTURE_ROOT", tmp_path / "does-not-exist"
        )
        with pytest.raises(FileNotFoundError):
            InboxClient()
