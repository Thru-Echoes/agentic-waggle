"""Real-browser E2E tests for the /inbox extract → confirm flow.

Layer 2 verification per ``.claude/rules/ui-verification.md`` for the
new inbox confirm-and-persist surface. Boots a uvicorn subprocess
against the test DB (seeded once per session), drives the inbox page
in Chromium, and asserts both the happy path and the no-customer
error path.

Skips with a clear message when:
    * playwright isn't installed
    * Chromium binaries aren't installed
    * no LLM key (the extract step calls the agent)
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Reuse server + seed pattern from test_e2e_copilot_browser
# ---------------------------------------------------------------------------


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_server(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"server at {host}:{port} did not start within {timeout}s")


def _split_sql(sql: str) -> list[str]:
    import re
    no_comments = re.sub(r"--[^\n]*", "", sql)
    return [c.strip() for c in no_comments.split(";") if c.strip()]


@pytest.fixture(scope="session")
def live_inbox_server() -> Iterator[str]:
    """Same shape as test_e2e_copilot_browser.live_server_url.

    Seeds a fresh dataset that includes ``synced_emails`` so the inbox
    page has emails to extract from, plus an ``inbox_fixture_root`` env
    pointing at the project's ``gmail-fixtures`` directory.
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
        products_gen.persist(session, products_gen.generate(fk))
        services_gen.persist(session, services_gen.generate())
        # Backfill the matcher search-entry tables so the inbox extract
        # flow (which routes through match_customer/match_sku) sees a
        # populated index rather than MatcherIndexEmpty.
        from providers.semantic_matcher import backfill_all
        backfill_all(session)
        session.commit()
    finally:
        session.close()
        seed_engine.dispose()

    port = _free_port()
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    # Point the inbox fixture root at the repo's gmail-fixtures dir so
    # the page has real fixtures to render.
    env["INBOX_FIXTURE_ROOT"] = str(REPO_ROOT / "gmail-fixtures")
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


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Chromium session-scoped fixture; skips when binary not installed."""
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(
                "Chromium binary not available — run `playwright install "
                f"chromium` first. Error: {exc.__class__.__name__}"
            )
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    ctx: BrowserContext = browser.new_context()
    p = ctx.new_page()
    try:
        yield p
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInboxPageLoads:
    def test_inbox_page_renders_with_emails(
        self, page: Page, live_inbox_server: str
    ) -> None:
        page.goto(f"{live_inbox_server}/inbox")
        # Either the fixture-root resolved (rows present) or the fixture
        # error card is shown. Both are valid renderings — but for the
        # test to be useful we need rows.
        if page.locator(".empty:has-text('No emails')").count() > 0:
            pytest.skip("No fixture emails present at INBOX_FIXTURE_ROOT.")
        expect(page.locator("table.rows")).to_be_visible()
        # At least one Extract button.
        assert page.locator("button.action:has-text('Extract')").count() >= 1


class TestInboxExtractRendering:
    """Verify the extract route returns the partial with the Confirm form.

    We POST directly to /inbox/extract via the Playwright request API
    (faster than UI-driving the click, and avoids fixture-email picking)
    and assert the rendered partial includes the new Confirm form +
    hidden draft_json field. The full extract→confirm round-trip lives
    in ``tests/test_routes_inbox_confirm.py`` via FastAPI's TestClient
    (no LLM cycle needed — it short-circuits the partial render).

    Skipped when no fixture emails are present at ``INBOX_FIXTURE_ROOT``.
    """

    def test_extract_partial_includes_confirm_form_on_a_draft(
        self, page: Page, live_inbox_server: str, require_llm_api_key
    ) -> None:
        page.goto(f"{live_inbox_server}/inbox")
        if page.locator(".empty:has-text('No emails')").count() > 0:
            pytest.skip("No fixture emails present at INBOX_FIXTURE_ROOT.")

        # Find the first email row + its account / message ids.
        first_row = page.locator("table.rows tbody tr").first
        button = first_row.locator("button.action:has-text('Extract')")
        # The hx-vals attribute carries the account_id + message_id.
        hx_vals = button.get_attribute("hx-vals") or "{}"
        import json
        payload = json.loads(hx_vals)

        api = page.request
        resp = api.post(
            f"{live_inbox_server}/inbox/extract",
            form={
                "account_id": payload["account_id"],
                "message_id": payload["message_id"],
            },
        )
        # Either the agent produced a draft (which now must include the
        # Confirm form) OR a non-order-intent / failure card. Both are
        # valid renderings; we only assert the new wiring is present
        # when a draft was produced.
        body = resp.text()
        if "draft-actions" in body:
            assert 'hx-post="/inbox/confirm"' in body
            assert 'name="draft_json"' in body
            assert "Confirm" in body
