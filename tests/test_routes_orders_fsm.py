"""/orders list pagination, the detail partial, and the transition action.

Real app + real Postgres through the ``db`` fixture (fresh schema per test);
no model is involved on these paths.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.main import create_app
from db.enums import OrderState
from db.models import Order
from db.session import get_db_session


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("WAGGLE_SKIP_PROVIDER_PROBE", "1")
    monkeypatch.delenv("WAGGLE_USER", raising=False)
    monkeypatch.delenv("WAGGLE_PASS", raising=False)
    get_settings.cache_clear()
    app = create_app()

    def _override():
        yield db

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _seed_people(db, faker_instance) -> tuple[uuid.UUID, uuid.UUID]:
    from seed import apiarists as apiarists_gen
    from seed import customers as customers_gen
    from seed import regions as regions_gen

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers_gen.persist(db, customers_gen.generate(faker_instance))
    apiarists_gen.persist(db, apiarists_gen.generate(faker_instance, regions))
    db.commit()
    cid = db.execute(text("SELECT id FROM customers ORDER BY name LIMIT 1")).scalar_one()
    uid = db.execute(text("SELECT id FROM users WHERE role='apiarist' ORDER BY email LIMIT 1")).scalar_one()
    return uuid.UUID(str(cid)), uuid.UUID(str(uid))


def _make_orders(db, faker_instance, n: int, state: OrderState = OrderState.EXTRACTED) -> list[Order]:
    cid, uid = _seed_people(db, faker_instance)
    out: list[Order] = []
    for i in range(n):
        o = Order(customer_id=cid, user_id=uid, state=state, total_usd=Decimal(f"{100 + i}.00"))
        db.add(o)
        out.append(o)
    db.commit()
    for o in out:
        db.refresh(o)
    return out


class TestOrdersList:
    def test_pagination_shows_counts_and_links(self, client: TestClient, db, faker_instance) -> None:
        _make_orders(db, faker_instance, 7)
        resp = client.get("/orders?limit=3")
        assert resp.status_code == 200
        assert "Showing 1–3 of 7" in resp.text
        assert "Next →" in resp.text and "← Previous" not in resp.text
        resp = client.get("/orders?limit=3&offset=6")
        assert "Showing 7–7 of 7" in resp.text
        assert "← Previous" in resp.text and "Next →" not in resp.text

    def test_filter_is_preserved_in_pagination_links(self, client: TestClient, db, faker_instance) -> None:
        _make_orders(db, faker_instance, 4, state=OrderState.APPROVED)
        resp = client.get("/orders?state=APPROVED&limit=2")
        assert "Showing 1–2 of 4" in resp.text
        assert "offset=2&amp;limit=2&amp;state=APPROVED" in resp.text

    def test_rows_are_keyboard_reachable_and_load_the_partial(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        resp = client.get("/orders")
        assert f'hx-get="/orders/{order.id}/partial"' in resp.text
        assert 'tabindex="0"' in resp.text and 'role="button"' in resp.text
        assert "keyup[" not in resp.text  # no eval-based hx-trigger filters under the CSP config

    def test_empty_state(self, client: TestClient, db, faker_instance) -> None:
        _seed_people(db, faker_instance)
        resp = client.get("/orders?state=CANCELLED")
        assert resp.status_code == 200
        assert "No orders match this filter." in resp.text

    def test_bad_state_filter_is_a_422_not_a_500(self, client: TestClient, db, faker_instance) -> None:
        _seed_people(db, faker_instance)
        assert client.get("/orders?state=BOGUS").status_code == 422


class TestOrderDetail:
    def test_partial_shows_history_and_legal_next_moves(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        resp = client.get(f"/orders/{order.id}/partial")
        assert resp.status_code == 200
        body = resp.text
        assert "Transition history" in body
        assert "No transitions recorded" in body  # inserted directly, no creation row
        # EXTRACTED → UNDER_REVIEW | CANCELLED only
        assert 'value="UNDER_REVIEW"' in body and 'value="CANCELLED"' in body
        assert 'value="APPROVED"' not in body
        assert f'id="order-state-{order.id}" hx-swap-oob="true"' in body

    def test_unknown_order_is_404(self, client: TestClient, db, faker_instance) -> None:
        _seed_people(db, faker_instance)
        assert client.get(f"/orders/{uuid.uuid4()}/partial").status_code == 404

    def test_terminal_state_has_no_buttons(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1, state=OrderState.CANCELLED)
        body = client.get(f"/orders/{order.id}/partial").text
        assert "terminal state" in body
        assert "<form" not in body


class TestOrderTransition:
    def test_legal_move_updates_state_history_and_row_badge(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        resp = client.post(
            f"/orders/{order.id}/transition",
            data={"to_state": "UNDER_REVIEW", "expected_from_state": "EXTRACTED"},
        )
        assert resp.status_code == 200
        assert "Moved to UNDER_REVIEW." in resp.text
        assert db.execute(text("SELECT state FROM orders WHERE id=:id"), {"id": str(order.id)}).scalar_one() == "UNDER_REVIEW"
        rows = db.execute(
            text("SELECT from_state, to_state, metadata->>'source' FROM order_state_transitions WHERE order_id=:id"),
            {"id": str(order.id)},
        ).all()
        assert rows == [("EXTRACTED", "UNDER_REVIEW", "orders_ui")]
        # out-of-band badge for the list row carries the new state
        assert f'id="order-state-{order.id}" hx-swap-oob="true"\n      class="badge under_review">UNDER_REVIEW' in resp.text
        # the pane now offers the next legal moves
        assert 'name="to_state" value="APPROVED"' in resp.text
        assert 'name="to_state" value="UNDER_REVIEW"' not in resp.text  # the hidden expected_from_state input still carries it

    def test_illegal_move_is_409_and_writes_nothing(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        resp = client.post(
            f"/orders/{order.id}/transition",
            data={"to_state": "SYNCED_TO_ERP", "expected_from_state": "EXTRACTED"},
        )
        assert resp.status_code == 409
        assert "cannot move from EXTRACTED to SYNCED_TO_ERP" in resp.text
        assert db.execute(text("SELECT COUNT(*) FROM order_state_transitions")).scalar_one() == 0
        assert db.execute(text("SELECT state FROM orders WHERE id=:id"), {"id": str(order.id)}).scalar_one() == "EXTRACTED"

    def test_stale_pane_is_409_and_re_rendered_from_current_state(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        first = client.post(
            f"/orders/{order.id}/transition",
            data={"to_state": "UNDER_REVIEW", "expected_from_state": "EXTRACTED"},
        )
        assert first.status_code == 200
        # A second tab still showing EXTRACTED tries the same move.
        stale = client.post(
            f"/orders/{order.id}/transition",
            data={"to_state": "UNDER_REVIEW", "expected_from_state": "EXTRACTED"},
        )
        assert stale.status_code == 409
        assert "is in state UNDER_REVIEW, not EXTRACTED" in stale.text
        assert 'name="to_state" value="APPROVED"' in stale.text  # pane shows the real current moves
        assert db.execute(text("SELECT COUNT(*) FROM order_state_transitions")).scalar_one() == 1

    def test_double_submit_second_click_is_rejected(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        data = {"to_state": "UNDER_REVIEW", "expected_from_state": "EXTRACTED"}
        a = client.post(f"/orders/{order.id}/transition", data=data)
        b = client.post(f"/orders/{order.id}/transition", data=data)
        assert (a.status_code, b.status_code) == (200, 409)

    def test_malformed_state_is_422(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        resp = client.post(f"/orders/{order.id}/transition", data={"to_state": "NOPE", "expected_from_state": "EXTRACTED"})
        assert resp.status_code == 422

    def test_full_walk_to_erp_through_the_ui(self, client: TestClient, db, faker_instance) -> None:
        (order,) = _make_orders(db, faker_instance, 1)
        path = ["UNDER_REVIEW", "APPROVED", "SCHEDULED", "IN_PROGRESS", "FULFILLED", "INVOICED", "SYNCED_TO_ERP"]
        current = "EXTRACTED"
        for nxt in path:
            resp = client.post(f"/orders/{order.id}/transition", data={"to_state": nxt, "expected_from_state": current})
            assert resp.status_code == 200, (nxt, resp.text[:200])
            current = nxt
        body = client.get(f"/orders/{order.id}/partial").text
        assert "terminal state" in body
        assert body.count("orders_ui") == len(path)
