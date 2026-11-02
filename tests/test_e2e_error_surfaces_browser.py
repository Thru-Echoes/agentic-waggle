"""Real-browser checks for the error surfaces (Layer 2 of ui-verification.md).

Two servers are started per module: one with a syntactically valid but
rejected OpenAI key (the exact failure the live demo had), and one with
Basic auth enabled and the demo credentials marked public. No real LLM
key is needed: the dead-key path is the point. The rejected call reaches
the provider and costs nothing.
"""
from __future__ import annotations

from typing import Iterator

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Page, expect  # noqa: E402

from tests._live_server import LiveServer, start_live_server  # noqa: E402

DEAD_KEY = "sk-proj-not-a-real-key-for-the-waggle-error-surface-tests"


@pytest.fixture(scope="module")
def badkey_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    server = start_live_server(
        tmp_path_factory.mktemp("badkey"),
        env_overrides={"OPENAI_API_KEY": DEAD_KEY, "LLM_PROBE_TTL_SECONDS": "10"},
        drop_env=("ANTHROPIC_API_KEY", "WAGGLE_USER", "WAGGLE_PASS"),
    )
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="module")
def auth_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    server = start_live_server(
        tmp_path_factory.mktemp("auth"),
        env_overrides={
            "WAGGLE_USER": "reviewer",
            "WAGGLE_PASS": "open-sesame",
            "WAGGLE_DEMO_CREDENTIALS_PUBLIC": "1",
            "WAGGLE_SKIP_PROVIDER_PROBE": "1",
        },
        drop_env=("OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
    )
    try:
        yield server
    finally:
        server.stop()


class TestDeadKeyIsVisible:
    def test_extract_shows_an_error_card_instead_of_nothing(
        self, page: Page, badkey_server: LiveServer
    ) -> None:
        page.goto(f"{badkey_server.base_url}/inbox")
        rows = page.locator("button.action:has-text('Extract')")
        assert rows.count() > 0, "inbox has no fixture rows to extract"
        rows.first.click()
        card = page.locator("#extract-pane .confirm-result.error")
        expect(card).to_be_visible(timeout=30_000)
        expect(card).to_contain_text("rejected this server's API key")
        expect(card).to_contain_text("Request id")
        # The pane no longer shows the untouched placeholder, and the
        # provider's message (which names the key) never reaches the page.
        assert "Click Extract on a row" not in page.locator("#extract-pane").inner_text()
        assert "sk-proj" not in page.content()

    def test_ready_reports_degraded_with_the_reason(
        self, page: Page, badkey_server: LiveServer
    ) -> None:
        resp = page.request.get(f"{badkey_server.base_url}/ready")
        assert resp.status == 200
        body = resp.json()
        assert body["database"] == "ok" and body["pgvector"] == "ok"
        assert body["status"] == "degraded"
        assert body["llm"] in ("unauthorized", "unreachable")
        assert "sk-proj" not in resp.text()

    def test_health_carries_build_provenance_fields(
        self, page: Page, badkey_server: LiveServer
    ) -> None:
        body = page.request.get(f"{badkey_server.base_url}/health").json()
        assert body["status"] == "ok"
        assert "git_sha" in body and "built_at" in body and body["version"]


class TestFooterAndHeaders:
    def test_footer_states_synthetic_data_and_links_docs(
        self, page: Page, badkey_server: LiveServer
    ) -> None:
        page.goto(f"{badkey_server.base_url}/")
        footer = page.locator("footer")
        expect(footer).to_contain_text("synthetic demo data")
        docs_link = footer.locator("a[href='/docs']")
        expect(docs_link).to_be_visible()
        assert page.request.get(f"{badkey_server.base_url}/docs").status == 200

    def test_pages_render_under_the_csp_with_vendored_htmx(
        self, page: Page, badkey_server: LiveServer
    ) -> None:
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        resp = page.goto(f"{badkey_server.base_url}/customers")
        assert resp is not None and resp.status == 200
        assert "content-security-policy" in resp.headers  # Playwright lowercases header names
        assert "unpkg.com" not in page.content()
        # HTMX booted (it registers its global) and the CSP blocked nothing it needs.
        assert page.evaluate("typeof window.htmx") == "object"
        assert not [e for e in errors if "Content Security Policy" in e], errors


class TestBrowserArrivalWithAuth:
    def test_cancelling_the_dialog_shows_a_helpful_page(
        self, page: Page, auth_server: LiveServer
    ) -> None:
        # The APIRequestContext sends no credentials, so this is the body a
        # browser renders after the Basic dialog is dismissed.
        resp = page.request.get(f"{auth_server.base_url}/", headers={"Accept": "text/html"})
        assert resp.status == 401
        assert resp.headers.get("www-authenticate", "").startswith("Basic realm=")
        html = resp.text()
        assert "synthetic data" in html
        assert "reviewer" in html and "open-sesame" in html
        assert "Back to the portfolio page" in html

    def test_credentials_open_the_dashboard(self, page: Page, auth_server: LiveServer) -> None:
        ctx = page.context.browser.new_context(  # type: ignore[union-attr]
            http_credentials={"username": "reviewer", "password": "open-sesame"}
        )
        try:
            authed = ctx.new_page()
            resp = authed.goto(f"{auth_server.base_url}/")
            assert resp is not None and resp.status == 200
            expect(authed.locator("h1")).to_contain_text("Dashboard")
        finally:
            ctx.close()
