"""POST /copilot/turns/{id}/confirm-match — resume a paused turn.

Covers:
  * commit path: customer_id provided → new assistant turn inserted
  * reject path: reject=1 → pick-match HTML returned
  * 404 on unknown turn_id
  * 409 when turn has no pending_customer_match marker
  * 400 when customer_id is absent and reject is False

All tests use real Postgres via the ``db`` fixture; no LLM calls required.
The ``pending_customer_match`` marker is seeded directly via SQL (the
orchestrator integration that creates it lands in Task 12).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from db.session import get_db_session


# ---------------------------------------------------------------------------
# Test client with DB override
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db) -> Iterator[TestClient]:
    """FastAPI TestClient with get_db_session overridden to use the fixture session."""
    app = create_app()

    def _override():
        yield db

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_customer(db) -> str:
    """Insert a minimal customer row and return its id string.

    The ``pending_customer_match`` alternatives array references a customer_id.
    The route only queries ``customers`` by id (no FK on chat_turns.tool_calls),
    but ``resume_with_customer`` does a SELECT against ``customers`` to get the
    name — so we need at least one real row.

    Side effects: INSERTs into customers.
    """
    cid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO customers (id, name, erp_customer_no, market_segment) "
            "VALUES (:cid, 'Test Orchard', 'ERP-001', 'almonds')"
        ),
        {"cid": cid},
    )
    db.commit()
    return cid


def _seed_conv_and_pending_turn(
    db,
    *,
    target_intent: str = "log_customer_report",
    hint: str = "the orchard",
) -> tuple[str, str, str]:
    """Insert a conversation + an assistant turn with a pending_customer_match marker.

    Also seeds a minimal customer row so resume_with_customer can look up
    the customer name.

    Returns ``(conv_id_str, turn_id_str, customer_id_str)``.

    Side effects: INSERTs into customers, conversations, chat_turns.
    """
    target_cid = _seed_customer(db)

    conv_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO conversations (id) VALUES (:cid)"),
        {"cid": conv_id},
    )

    turn_id = str(uuid.uuid4())
    pending_record = json.dumps([
        {
            "tool_name": "pending_customer_match",
            "target_intent": target_intent,
            "hint": hint,
            "alternatives": [
                [target_cid, "Test Orchard", 0.6],
            ],
        }
    ])
    # Use psycopg2 %(name)s style + explicit CAST to avoid the sqlalchemy
    # named-param / postgres cast collision with :name::type syntax.
    db.execute(
        text(
            "INSERT INTO chat_turns "
            "(id, conversation_id, role, text, tool_calls) "
            "VALUES (:tid, :cid, 'assistant', 'Pending match', "
            "CAST(:tc AS jsonb))"
        ),
        {"tid": turn_id, "cid": conv_id, "tc": pending_record},
    )
    db.commit()
    return conv_id, turn_id, target_cid


def _seed_conv_and_plain_turn(db) -> tuple[str, str]:
    """Insert a conversation + an assistant turn with NO pending_customer_match marker."""
    conv_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO conversations (id) VALUES (:cid)"),
        {"cid": conv_id},
    )
    turn_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO chat_turns "
            "(id, conversation_id, role, text, tool_calls) "
            "VALUES (:tid, :cid, 'assistant', 'No match here', "
            "CAST(:tc AS jsonb))"
        ),
        {"tid": turn_id, "cid": conv_id, "tc": json.dumps([])},
    )
    db.commit()
    return conv_id, turn_id


# ---------------------------------------------------------------------------
# Tests: commit path (customer_id provided)
# ---------------------------------------------------------------------------


class TestConfirmMatchCommit:
    def test_returns_200_and_inserts_assistant_turn(self, client, db) -> None:
        """Committing a customer_id inserts a new assistant ChatTurn."""
        _, turn_id, target_cid = _seed_conv_and_pending_turn(db)

        before = int(
            db.execute(text("SELECT COUNT(*) FROM chat_turns")).scalar_one()
        )
        resp = client.post(
            f"/copilot/turns/{turn_id}/confirm-match",
            data={"customer_id": target_cid},
        )
        assert resp.status_code == 200
        after = int(
            db.execute(text("SELECT COUNT(*) FROM chat_turns")).scalar_one()
        )
        assert after == before + 1, (
            "Expected one new assistant ChatTurn to be inserted on commit"
        )

    def test_response_body_is_assistant_partial_html(self, client, db) -> None:
        """Response body contains the assistant bubble HTML fragment."""
        _, turn_id, target_cid = _seed_conv_and_pending_turn(db)

        resp = client.post(
            f"/copilot/turns/{turn_id}/confirm-match",
            data={"customer_id": target_cid},
        )
        assert resp.status_code == 200
        # The copilot_turn_assistant.html partial marks the bubble with data-role="assistant".
        assert 'data-role="assistant"' in resp.text

    def test_clears_pending_marker_from_turn(self, client, db) -> None:
        """After commit, the paused turn's tool_calls no longer has the pending marker."""
        _, turn_id, target_cid = _seed_conv_and_pending_turn(db)

        client.post(
            f"/copilot/turns/{turn_id}/confirm-match",
            data={"customer_id": target_cid},
        )
        row = db.execute(
            text("SELECT tool_calls FROM chat_turns WHERE id = :tid"),
            {"tid": turn_id},
        ).mappings().first()
        assert row is not None
        tool_calls = row["tool_calls"] or []
        pending_names = [tc.get("tool_name") for tc in tool_calls]
        assert "pending_customer_match" not in pending_names, (
            "pending_customer_match marker should be cleared after commit"
        )

    def test_create_order_intent_also_works(self, client, db) -> None:
        """create_order intent goes through resume_create_order without error."""
        _, turn_id, target_cid = _seed_conv_and_pending_turn(
            db, target_intent="create_order", hint="queen order"
        )
        resp = client.post(
            f"/copilot/turns/{turn_id}/confirm-match",
            data={"customer_id": target_cid},
        )
        assert resp.status_code == 200
        assert 'data-role="assistant"' in resp.text


# ---------------------------------------------------------------------------
# Tests: reject path (reject=1)
# ---------------------------------------------------------------------------


class TestConfirmMatchReject:
    def test_reject_returns_200(self, client, db) -> None:
        """reject=1 returns HTTP 200."""
        _, turn_id, _ = _seed_conv_and_pending_turn(db)
        resp = client.post(
            f"/copilot/turns/{turn_id}/confirm-match?reject=1",
        )
        assert resp.status_code == 200

    def test_reject_body_contains_pick_match_html(self, client, db) -> None:
        """reject=1 swaps in the pick-match card."""
        _, turn_id, _ = _seed_conv_and_pending_turn(db)
        resp = client.post(
            f"/copilot/turns/{turn_id}/confirm-match?reject=1",
        )
        body = resp.text
        # Either the wrapper class or the literal template name signals the right partial.
        assert "match-pick-card" in body or "pick-match" in body.lower(), (
            f"Expected pick-match HTML in response body; got: {body[:300]!r}"
        )

    def test_reject_does_not_insert_new_turn(self, client, db) -> None:
        """reject=1 should not create a new ChatTurn row."""
        _, turn_id, _ = _seed_conv_and_pending_turn(db)
        before = int(
            db.execute(text("SELECT COUNT(*) FROM chat_turns")).scalar_one()
        )
        client.post(
            f"/copilot/turns/{turn_id}/confirm-match?reject=1",
        )
        after = int(
            db.execute(text("SELECT COUNT(*) FROM chat_turns")).scalar_one()
        )
        assert after == before, (
            "reject path must not insert a new ChatTurn"
        )

    def test_reject_preserves_pending_marker(self, client, db) -> None:
        """reject=1 leaves the pending_customer_match marker intact."""
        _, turn_id, _ = _seed_conv_and_pending_turn(db)
        client.post(
            f"/copilot/turns/{turn_id}/confirm-match?reject=1",
        )
        row = db.execute(
            text("SELECT tool_calls FROM chat_turns WHERE id = :tid"),
            {"tid": turn_id},
        ).mappings().first()
        assert row is not None
        tool_calls = row["tool_calls"] or []
        names = [tc.get("tool_name") for tc in tool_calls]
        assert "pending_customer_match" in names, (
            "pending_customer_match must still be present after reject"
        )


# ---------------------------------------------------------------------------
# Tests: error paths
# ---------------------------------------------------------------------------


class TestConfirmMatchErrors:
    def test_unknown_turn_id_returns_404(self, client, db) -> None:
        """Turn that doesn't exist in the DB returns 404."""
        resp = client.post(
            f"/copilot/turns/{uuid.uuid4()}/confirm-match",
            data={"customer_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404

    def test_turn_without_pending_marker_returns_409(self, client, db) -> None:
        """Turn that exists but has no pending_customer_match returns 409."""
        _, turn_id = _seed_conv_and_plain_turn(db)
        resp = client.post(
            f"/copilot/turns/{turn_id}/confirm-match",
            data={"customer_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 409

    def test_missing_customer_id_without_reject_returns_400(self, client, db) -> None:
        """No customer_id and no reject=1 returns 400."""
        _, turn_id, _ = _seed_conv_and_pending_turn(db)
        resp = client.post(
            f"/copilot/turns/{turn_id}/confirm-match",
            # No customer_id in form body
        )
        assert resp.status_code == 400
