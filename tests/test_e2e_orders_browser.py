"""Real-browser walk of the orders page: click a row, advance an order, watch
the row badge and the history update in place (Layer 2 of ui-verification.md).
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Iterator

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Page, expect  # noqa: E402

from tests._live_server import LiveServer, start_live_server  # noqa: E402


@pytest.fixture(scope="module")
def orders_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    server = start_live_server(
        tmp_path_factory.mktemp("orders"),
        env_overrides={"WAGGLE_SKIP_PROVIDER_PROBE": "1"},
        drop_env=("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WAGGLE_USER", "WAGGLE_PASS"),
    )
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="module")
def seeded_order_id(orders_server: LiveServer) -> uuid.UUID:
    """Insert one EXTRACTED order after the server booted (it reads the DB live)."""
    import os

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from db.models import Order
    from db.url import get_sqlalchemy_url

    url = get_sqlalchemy_url(os.environ.get("TEST_DATABASE_URL"))
    engine = create_engine(url, future=True)
    session = sessionmaker(bind=engine, future=True, expire_on_commit=False)()
    try:
        cid = session.execute(text("SELECT id FROM customers ORDER BY name LIMIT 1")).scalar_one()
        uid = session.execute(text("SELECT id FROM users WHERE role='apiarist' ORDER BY email LIMIT 1")).scalar_one()
        order = Order(customer_id=uuid.UUID(str(cid)), user_id=uuid.UUID(str(uid)),
                      state="EXTRACTED", total_usd=Decimal("1234.50"))  # type: ignore[arg-type]
        session.add(order)
        session.commit()
        return order.id
    finally:
        session.close()
        engine.dispose()


class TestOrdersWalk:
    def test_row_click_opens_detail_and_advance_updates_badge(
        self, page: Page, orders_server: LiveServer, seeded_order_id: uuid.UUID
    ) -> None:
        page.goto(f"{orders_server.base_url}/orders")
        expect(page.locator("#orders-pagination")).to_contain_text("Showing 1–1 of 1")
        row = page.locator(f"tr.clickable:has(#order-state-{seeded_order_id})")
        expect(row.locator(".badge")).to_have_text("EXTRACTED")

        row.click()
        pane = page.locator("#order-detail-pane")
        expect(pane).to_contain_text("Transition history", timeout=10_000)
        expect(pane).to_contain_text("No transitions recorded")
        expect(pane.locator("button[value='UNDER_REVIEW']")).to_be_visible()

        pane.locator("button[value='UNDER_REVIEW']").click()
        expect(pane).to_contain_text("Moved to UNDER_REVIEW.", timeout=10_000)
        # row badge updated out-of-band, without a page reload
        expect(page.locator(f"#order-state-{seeded_order_id}").first).to_have_text("UNDER_REVIEW")
        # next legal moves offered; the previous one is gone
        expect(pane.locator("button[value='APPROVED']")).to_be_visible()
        expect(pane.locator("button[value='UNDER_REVIEW']")).to_have_count(0)

    def test_keyboard_opens_row_and_back_forward_keeps_page(
        self, page: Page, orders_server: LiveServer, seeded_order_id: uuid.UUID
    ) -> None:
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(f"{orders_server.base_url}/orders")
        row = page.locator("tr.clickable").first
        row.focus()
        page.keyboard.press("a")  # a non-activating key must not open the row
        page.wait_for_timeout(300)
        expect(page.locator("#order-detail-pane")).to_contain_text("Select an order")
        page.keyboard.press("Enter")
        expect(page.locator("#order-detail-pane")).to_contain_text("Transition history", timeout=10_000)
        assert not [e for e in errors if "evalDisallowed" in e or "Content Security Policy" in e], errors
        page.goto(f"{orders_server.base_url}/orders?state=UNDER_REVIEW")
        page.go_back()
        expect(page.locator("h1")).to_have_text("Orders")

    def test_double_click_on_advance_only_moves_once(
        self, page: Page, orders_server: LiveServer, seeded_order_id: uuid.UUID
    ) -> None:
        page.goto(f"{orders_server.base_url}/orders")
        page.locator(f"tr.clickable:has(#order-state-{seeded_order_id})").click()
        pane = page.locator("#order-detail-pane")
        btn = pane.locator("button[value='APPROVED']")
        expect(btn).to_be_visible(timeout=10_000)
        btn.dblclick()  # hx-disabled-elt disables the button during the request
        expect(pane).to_contain_text("Moved to APPROVED.", timeout=10_000)
        expect(page.locator(f"#order-state-{seeded_order_id}").first).to_have_text("APPROVED")
        page.reload()
        page.locator(f"tr.clickable:has(#order-state-{seeded_order_id})").click()
        expect(pane).to_contain_text("UNDER_REVIEW", timeout=10_000)
        # exactly two transitions recorded overall (EXTRACTED→UNDER_REVIEW, UNDER_REVIEW→APPROVED)
        assert pane.locator(".order-history li").count() == 2
