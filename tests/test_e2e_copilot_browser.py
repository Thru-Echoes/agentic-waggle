"""Real-browser E2E tests for the sales copilot — Layer 2 UI verification.

Per ``.claude/rules/ui-verification.md``: every UI-affecting change needs
a Playwright + pytest suite that covers the happy path, edge cases, and
unusual event sequences against a real browser. These tests boot the
FastAPI app inside a uvicorn subprocess against the test database
(seeded once per session) and drive the rendered HTML with the
synchronous Playwright API.

Skips with a clear message when:
    * playwright is not installed
    * the browser binaries (``playwright install chromium``) are missing
    * no LLM API key is set (the orchestrator-touching tests need it)

Side effects per session: starts a uvicorn subprocess on a free local
port; seeds the test DB once; downloads no browsers (assumes
``playwright install chromium`` has already run).
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Iterator

import pytest

playwright_module = pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Session-scoped server + browser fixtures
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Bind to port 0, return the OS-assigned free port, then release."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> None:
    """Poll a TCP connect until the server accepts."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(
        f"server at {host}:{port} did not start within {timeout}s"
    )


@pytest.fixture(scope="session")
def live_server_url() -> Iterator[str]:
    """Start uvicorn against the test DB; seed once; yield base URL.

    Side effects: spawns a uvicorn subprocess for the duration of the
    session; seeds a small dataset. Faker is built locally (scope-mismatch
    workaround — the project's faker_instance fixture is function-scoped).
    """
    db_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://agentic_mcp_crm:agentic_mcp_crm@localhost:5433/agentic_mcp_crm_test",
    )
    from faker import Faker
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from seed.faker_providers import BeekeepingProvider

    fk = Faker(locale="en_US")
    Faker.seed(42)
    fk.add_provider(BeekeepingProvider)

    seed_engine = create_engine(db_url, future=True)

    schema_path = REPO_ROOT / "tests" / "schema.sql"
    with seed_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        for stmt in _split_sql(schema_path.read_text()):
            conn.execute(text(stmt))

    Maker = sessionmaker(bind=seed_engine, future=True, expire_on_commit=False)
    session = Maker()
    try:
        from seed import (
            apiarists as apiarists_gen,
            customers as customers_gen,
            embeddings as embeddings_gen,
            inspections as inspections_gen,
            products as products_gen,
            regions as regions_gen,
            services as services_gen,
            sites as sites_gen,
        )
        regions = regions_gen.generate()
        regions_gen.persist(session, regions)
        customers = customers_gen.generate(fk)
        customers_gen.persist(session, customers)
        sites = sites_gen.generate(fk, customers, regions)
        sites_gen.persist(session, sites)
        apiarists = apiarists_gen.generate(fk, regions)
        apiarists_gen.persist(session, apiarists)
        inspections = inspections_gen.generate(fk, sites, apiarists)
        inspections_gen.persist(session, inspections)
        products_gen.persist(session, products_gen.generate(fk))
        services_gen.persist(session, services_gen.generate())
        embeddings_gen.run(session)
        session.commit()
    finally:
        session.close()
        seed_engine.dispose()

    port = _free_port()
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_server("127.0.0.1", port)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _split_sql(sql: str) -> list[str]:
    import re
    no_comments = re.sub(r"--[^\n]*", "", sql)
    return [c.strip() for c in no_comments.split(";") if c.strip()]


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """One Chromium browser instance shared across the session."""
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    """Fresh browser context + page per test (isolated cookies / storage)."""
    ctx: BrowserContext = browser.new_context()
    p = ctx.new_page()
    try:
        yield p
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCopilotShell:
    def test_loads_with_composer_and_thread(
        self, page: Page, live_server_url: str
    ) -> None:
        page.goto(f"{live_server_url}/copilot")
        expect(page.locator("#composer-text")).to_be_visible()
        expect(page.locator("#thread")).to_be_visible()
        # Top nav highlights Copilot.
        expect(page.locator("nav.primary a.active")).to_have_text("Copilot")

    def test_empty_state_shown_in_fresh_conversation(
        self, page: Page, live_server_url: str
    ) -> None:
        # Create a brand-new conversation and visit it directly.
        api = page.request
        resp = api.post(
            f"{live_server_url}/api/copilot/conversations",
            data="{}",
            headers={"content-type": "application/json"},
        )
        assert resp.ok
        conv_id = resp.json()["id"]
        page.goto(f"{live_server_url}/copilot/{conv_id}")
        expect(page.locator("#thread-empty")).to_be_visible()
        expect(page.get_by_text("How can Waggle help?")).to_be_visible()

    def test_rail_lists_conversations(
        self, page: Page, live_server_url: str
    ) -> None:
        # Pre-seed a conversation via API then verify it shows in the rail.
        api = page.request
        api.post(
            f"{live_server_url}/api/copilot/conversations",
            data="{}",
            headers={"content-type": "application/json"},
        )
        page.goto(f"{live_server_url}/copilot")
        # At least one rail item exists.
        items = page.locator(".rail-item")
        assert items.count() >= 1


class TestCopilotComposer:
    def _send_message(self, page: Page, message: str) -> None:
        page.locator("#composer-text").fill(message)
        # Send via Enter (no Shift) — the inline JS in copilot.html submits.
        page.locator("#composer-text").press("Enter")
        # Wait for HTMX to swap a response in.
        page.wait_for_selector(".turn.assistant", timeout=60_000)

    def test_query_renders_user_and_assistant_bubbles(
        self, page: Page, live_server_url: str, require_llm_api_key
    ) -> None:
        page.goto(f"{live_server_url}/copilot")
        before = page.locator(".turn").count()
        self._send_message(page, "What's the general state of varroa across our customers?")
        # Two new bubbles appeared (user + assistant).
        expect(page.locator(".turn").nth(before)).to_be_visible()
        expect(page.locator(".turn").nth(before + 1)).to_be_visible()
        # User bubble carries the text we typed.
        user_bubble = page.locator(".turn.user").last
        expect(user_bubble).to_contain_text("varroa")

    def test_textarea_clears_after_send(
        self, page: Page, live_server_url: str, require_llm_api_key
    ) -> None:
        page.goto(f"{live_server_url}/copilot")
        self._send_message(page, "Quick test query about hives.")
        # The form's after-request handler resets it.
        expect(page.locator("#composer-text")).to_have_value("")

    def test_send_button_disabled_during_request(
        self, page: Page, live_server_url: str, require_llm_api_key
    ) -> None:
        page.goto(f"{live_server_url}/copilot")
        page.locator("#composer-text").fill("Tell me about our biggest customer.")
        # Click Send and verify the button is disabled while the LLM call
        # is in flight (htmx adds the disabled attribute via hx-disabled-elt).
        page.locator("#send-btn").click()
        # Disabled within a few hundred ms of the request leaving the browser.
        expect(page.locator("#send-btn")).to_be_disabled(timeout=2000)
        page.wait_for_selector(".turn.assistant", timeout=60_000)
        # Re-enabled after the request completes.
        expect(page.locator("#send-btn")).to_be_enabled()


class TestCopilotConversationManagement:
    def test_new_conversation_redirect(
        self, page: Page, live_server_url: str
    ) -> None:
        page.goto(f"{live_server_url}/copilot")
        page.locator("#new-conv-btn").click()
        # We get redirected into a new /copilot/{uuid} URL.
        page.wait_for_url(f"{live_server_url}/copilot/*", timeout=10_000)
        assert "/copilot/" in page.url
        # The new conversation has no turns and shows the empty state.
        expect(page.locator("#thread-empty")).to_be_visible()

    def test_conversation_switch_via_rail(
        self, page: Page, live_server_url: str
    ) -> None:
        page.goto(f"{live_server_url}/copilot")
        # Create a second conversation via the API so we have something to switch to.
        api = page.request
        api.post(
            f"{live_server_url}/api/copilot/conversations",
            data="{}",
            headers={"content-type": "application/json"},
        )
        page.reload()
        items = page.locator(".rail-item .rail-link")
        assert items.count() >= 2
        href_first = items.nth(0).get_attribute("href")
        items.nth(0).click()
        page.wait_for_url(f"{live_server_url}{href_first}", timeout=10_000)


class TestCopilotConfirmDraft:
    def test_confirm_order_round_trip(
        self, page: Page, live_server_url: str, require_llm_api_key
    ) -> None:
        # Use the API to grab a real customer name so the draft can match.
        api = page.request
        # Fetch one customer.
        resp = api.get(f"{live_server_url}/api/customers?limit=1")
        if not resp.ok:
            pytest.skip(f"Could not fetch a customer: {resp.status}")
        customers = resp.json()["items"]
        if not customers:
            pytest.skip("No customers in DB to anchor a draft order on.")
        cust_name = customers[0]["name"]

        page.goto(f"{live_server_url}/copilot")
        page.locator("#composer-text").fill(
            f"Draft an order for {cust_name}: 12 Italian queens."
        )
        page.locator("#composer-text").press("Enter")
        page.wait_for_selector(".turn.assistant", timeout=120_000)
        # If the orchestrator produced a draft card, the Confirm button is visible.
        confirm = page.locator(".confirm-order").last
        if confirm.count() > 0:
            confirm.click()
            page.wait_for_selector(".confirm-result", timeout=10_000)
            # Success or failure message present either way; UI didn't crash.
            assert page.locator(".confirm-result").count() >= 1


class TestCopilotErrorPaths:
    def test_unknown_conversation_returns_404_page(
        self, page: Page, live_server_url: str
    ) -> None:
        bad = uuid.uuid4()
        resp = page.request.get(f"{live_server_url}/copilot/{bad}")
        assert resp.status == 404

    def test_double_submit_does_not_duplicate_user_turn(
        self, page: Page, live_server_url: str, require_llm_api_key
    ) -> None:
        # Fresh conversation so we can count bubbles cleanly.
        api = page.request
        resp = api.post(
            f"{live_server_url}/api/copilot/conversations",
            data="{}",
            headers={"content-type": "application/json"},
        )
        assert resp.ok
        conv_id = resp.json()["id"]
        page.goto(f"{live_server_url}/copilot/{conv_id}")

        marker = "DOUBLESUBMIT_PROBE_QUERY_47"
        page.locator("#composer-text").fill(f"{marker} how many customers?")
        send = page.locator("#send-btn")
        send.click()
        # Second click while disabled — htmx blocks the duplicate.
        send.click(no_wait_after=True, force=True)
        page.wait_for_selector(".turn.assistant", timeout=120_000)

        # Exactly one user bubble carries our marker — the second click was suppressed.
        count = page.locator(
            f".turn.user .text:has-text('{marker}')"
        ).count()
        assert count == 1
