"""E2E tests for the inbox confirm-and-persist flow.

Mirrors the copilot's confirm-and-persist pattern: the extract route
returns a Jinja partial with the OrderDraft + a Confirm button; the
confirm route round-trips the draft JSON from a hidden form field,
re-validates it, and persists.

Real Postgres + real LLM (when key present). Side effects per call:
1 INSERT on orders + N on order_line_items + 1 on
order_state_transitions.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from decimal import Decimal
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agents.schemas import (
    CustomerMatch,
    OrderDraft,
    ResolvedLineItem,
)
from app.main import create_app
from db.session import get_db_session


# ---------------------------------------------------------------------------
# Test client + DB-override fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db) -> Iterator[TestClient]:
    app = create_app()

    def _override():
        yield db

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers — deterministic, no LLM needed
# ---------------------------------------------------------------------------


def _seed_min(db, faker_instance) -> None:
    """Customers + sites + apiarists + products + services (no orders/embeddings)."""
    from seed import apiarists as apiarists_gen
    from seed import customers as customers_gen
    from seed import products as products_gen
    from seed import regions as regions_gen
    from seed import services as services_gen
    from seed import sites as sites_gen

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    products_gen.persist(db, products_gen.generate(faker_instance))
    services_gen.persist(db, services_gen.generate())


def _build_matched_draft(db) -> OrderDraft:
    """Build an OrderDraft already pinned to a real customer + product.

    Used by the confirm tests so we don't have to run the LLM extraction
    just to exercise the persist path.
    """
    cust_row = db.execute(
        text("SELECT id, name, erp_customer_no FROM customers LIMIT 1")
    ).mappings().first()
    prod_row = db.execute(
        text("SELECT id, sku, price_usd FROM products LIMIT 1")
    ).mappings().first()

    return OrderDraft(
        customer=CustomerMatch(
            customer_id=cust_row["id"],
            customer_name=cust_row["name"],
            erp_customer_no=cust_row["erp_customer_no"],
            score=100.0,
            confidence=1.0,
            ambiguous=False,
            candidates_considered=[cust_row["name"]],
        ),
        line_items=[
            ResolvedLineItem(
                sku=prod_row["sku"],
                product_id=prod_row["id"],
                service_id=None,
                quantity=10,
                unit_price_usd=prod_row["price_usd"],
                match_method="exact_sku",
                source_description="10 units",
                evidence=[],
                confidence=1.0,
            ),
        ],
        total_usd=prod_row["price_usd"] * 10,
        overall_confidence=1.0,
        extraction_concerns=[],
        customer_evidence=[],
        pollination_date=None,
        source_email_external_id=None,
    )


def _build_unmatched_draft() -> OrderDraft:
    """Draft whose customer didn't resolve — used to exercise the error path."""
    return OrderDraft(
        customer=CustomerMatch(
            customer_id=None,
            customer_name=None,
            erp_customer_no=None,
            score=0.0,
            confidence=0.0,
            ambiguous=False,
            candidates_considered=[],
        ),
        line_items=[
            ResolvedLineItem(
                sku="UNKNOWN-1",
                product_id=None,
                service_id=None,
                quantity=1,
                unit_price_usd=Decimal("1.00"),
                match_method="llm_guess",
                source_description="placeholder",
                evidence=[],
                confidence=0.1,
            ),
        ],
        total_usd=Decimal("1.00"),
        overall_confidence=0.1,
        extraction_concerns=["customer did not match"],
        customer_evidence=[],
        pollination_date=None,
        source_email_external_id=None,
    )


def _count(db, table: str) -> int:
    return int(
        db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
    )


# ---------------------------------------------------------------------------
# Confirm route tests
# ---------------------------------------------------------------------------


class TestInboxConfirm:
    def test_confirm_persists_matched_draft(
        self, client, db, faker_instance
    ) -> None:
        _seed_min(db, faker_instance)
        db.commit()
        draft = _build_matched_draft(db)

        before_orders = _count(db, "orders")
        before_items = _count(db, "order_line_items")
        before_trans = _count(db, "order_state_transitions")

        resp = client.post(
            "/inbox/confirm",
            data={"draft_json": draft.model_dump_json()},
        )
        assert resp.status_code == 200
        assert "Saved" in resp.text
        assert _count(db, "orders") == before_orders + 1
        assert _count(db, "order_line_items") == before_items + len(draft.line_items)
        assert _count(db, "order_state_transitions") == before_trans + 1

    def test_confirm_unmatched_draft_returns_error(
        self, client, db, faker_instance
    ) -> None:
        _seed_min(db, faker_instance)
        db.commit()
        draft = _build_unmatched_draft()

        before_orders = _count(db, "orders")
        resp = client.post(
            "/inbox/confirm",
            data={"draft_json": draft.model_dump_json()},
        )
        assert resp.status_code == 200
        assert "Could not save" in resp.text
        assert "did not match" in resp.text
        # Nothing got persisted on the error path.
        assert _count(db, "orders") == before_orders

    def test_confirm_malformed_json_returns_error(self, client) -> None:
        resp = client.post(
            "/inbox/confirm",
            data={"draft_json": "{not valid json"},
        )
        assert resp.status_code == 200
        assert "Could not save" in resp.text
        assert "invalid draft payload" in resp.text.lower()

    def test_confirm_missing_draft_json_returns_422(self, client) -> None:
        # No draft_json field at all — FastAPI Form validation kicks in.
        resp = client.post("/inbox/confirm", data={})
        assert resp.status_code == 422

    def test_confirm_applies_qty_overrides(
        self, client, db, faker_instance
    ) -> None:
        """User edits the quantity field; persisted order should reflect it."""
        _seed_min(db, faker_instance)
        db.commit()
        draft = _build_matched_draft(db)
        original_qty = draft.line_items[0].quantity
        original_total = draft.total_usd
        edited_qty = original_qty * 2

        before_orders = _count(db, "orders")
        resp = client.post(
            "/inbox/confirm",
            data={
                "draft_json": draft.model_dump_json(),
                "qty_0": str(edited_qty),
            },
        )
        assert resp.status_code == 200
        assert "Saved" in resp.text
        assert _count(db, "orders") == before_orders + 1

        # The persisted line item should carry the doubled quantity.
        latest_order_id = db.execute(
            text("SELECT id FROM orders ORDER BY created_at DESC LIMIT 1")
        ).scalar_one()
        persisted_qty = db.execute(
            text(
                "SELECT quantity FROM order_line_items "
                "WHERE order_id = :oid"
            ),
            {"oid": str(latest_order_id)},
        ).scalar_one()
        assert int(persisted_qty) == edited_qty

        # Total should be recomputed: doubled qty = doubled total.
        persisted_total = db.execute(
            text("SELECT total_usd FROM orders WHERE id = :oid"),
            {"oid": str(latest_order_id)},
        ).scalar_one()
        from decimal import Decimal
        assert Decimal(str(persisted_total)) == original_total * 2

        # Transition metadata records the user edit for audit purposes.
        meta = db.execute(
            text(
                "SELECT metadata FROM order_state_transitions "
                "WHERE order_id = :oid"
            ),
            {"oid": str(latest_order_id)},
        ).scalar_one()
        assert meta.get("edited_by_user") is True

    def test_confirm_applies_pollination_date_override(
        self, client, db, faker_instance
    ) -> None:
        _seed_min(db, faker_instance)
        db.commit()
        draft = _build_matched_draft(db)

        from datetime import date
        target_date = date(2026, 6, 15)
        resp = client.post(
            "/inbox/confirm",
            data={
                "draft_json": draft.model_dump_json(),
                "pollination_date": target_date.isoformat(),
            },
        )
        assert resp.status_code == 200
        assert "Saved" in resp.text

        latest_pd = db.execute(
            text(
                "SELECT pollination_date FROM orders "
                "ORDER BY created_at DESC LIMIT 1"
            )
        ).scalar_one()
        assert latest_pd == target_date

    def test_confirm_ignores_unparseable_overrides(
        self, client, db, faker_instance
    ) -> None:
        """Garbage in the qty override field should fall back to the draft value."""
        _seed_min(db, faker_instance)
        db.commit()
        draft = _build_matched_draft(db)
        original_qty = draft.line_items[0].quantity

        resp = client.post(
            "/inbox/confirm",
            data={
                "draft_json": draft.model_dump_json(),
                "qty_0": "not a number",
                "unit_price_0": "abc",
            },
        )
        assert resp.status_code == 200
        assert "Saved" in resp.text

        latest_order_id = db.execute(
            text("SELECT id FROM orders ORDER BY created_at DESC LIMIT 1")
        ).scalar_one()
        persisted_qty = db.execute(
            text(
                "SELECT quantity FROM order_line_items "
                "WHERE order_id = :oid"
            ),
            {"oid": str(latest_order_id)},
        ).scalar_one()
        assert int(persisted_qty) == original_qty


# ---------------------------------------------------------------------------
# Extract → draft (renders the confirm button)
# ---------------------------------------------------------------------------


class TestInboxExtractRendersConfirmButton:
    """The extract partial now includes a Confirm form — regress-guard it."""

    def test_extract_partial_contains_confirm_form_when_matched(
        self, client, db, faker_instance
    ) -> None:
        # We don't run the full LLM extraction here — just render the
        # partial directly via Jinja the same way the route does.
        from pathlib import Path

        from fastapi.templating import Jinja2Templates
        from starlette.requests import Request

        _seed_min(db, faker_instance)
        db.commit()
        draft = _build_matched_draft(db)

        template_dir = Path(__file__).resolve().parent.parent / "app" / "templates"
        templates = Jinja2Templates(directory=str(template_dir))

        class _StubEmail:
            from_address = "test@example.test"
            subject = "test subject"

        rendered = templates.get_template("partials/agent_draft.html").render(
            draft=draft, email=_StubEmail(),
        )
        # Confirm form present + draft_json hidden field + active Confirm button.
        assert 'hx-post="/inbox/confirm"' in rendered
        assert 'name="draft_json"' in rendered
        assert "Confirm" in rendered
        # The button is NOT disabled for a matched draft.
        assert "disabled" not in rendered.split('class="action confirm-order"')[1].split(">")[0]

    def test_extract_partial_disables_confirm_when_no_customer(
        self, faker_instance
    ) -> None:
        from pathlib import Path

        from fastapi.templating import Jinja2Templates

        draft = _build_unmatched_draft()
        template_dir = Path(__file__).resolve().parent.parent / "app" / "templates"
        templates = Jinja2Templates(directory=str(template_dir))

        class _StubEmail:
            from_address = "test@example.test"
            subject = "test subject"

        rendered = templates.get_template("partials/agent_draft.html").render(
            draft=draft, email=_StubEmail(),
        )
        # Button is rendered but disabled.
        assert "disabled" in rendered
        assert "No customer match" in rendered
