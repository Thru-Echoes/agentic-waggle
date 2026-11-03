"""Real-browser checks for the copilot landing: example-prompt buttons fill
the composer, and two browsers never see each other's conversations.
No model is called (nothing is sent)."""
from __future__ import annotations

from typing import Iterator

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import Page, expect  # noqa: E402

from tests._live_server import LiveServer, start_live_server  # noqa: E402


@pytest.fixture(scope="module")
def copilot_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    server = start_live_server(
        tmp_path_factory.mktemp("copilot"),
        env_overrides={"WAGGLE_SKIP_PROVIDER_PROBE": "1"},
        drop_env=("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WAGGLE_USER", "WAGGLE_PASS"),
    )
    try:
        yield server
    finally:
        server.stop()


class TestLandingAndScoping:
    def test_example_prompt_button_fills_the_composer_without_sending(
        self, page: Page, copilot_server: LiveServer
    ) -> None:
        requests: list[str] = []
        page.on("request", lambda r: requests.append(r.url) if "/copilot/turns" in r.url else None)
        page.goto(f"{copilot_server.base_url}/copilot")
        expect(page.locator("#thread-empty")).to_contain_text("How can Waggle help?")
        buttons = page.locator("button.suggestion")
        assert buttons.count() == 3
        buttons.nth(2).click()
        ta = page.locator("#composer-text")
        expect(ta).to_have_value("Draft an order for Martinez & Sons Orchards — 20 Italian mated queens (QUEEN-ITA-MATED) at $48 each.")
        expect(ta).to_be_focused()
        assert requests == [], "clicking an example must not send a turn"

    def test_two_browsers_have_separate_conversation_rails(
        self, page: Page, copilot_server: LiveServer
    ) -> None:
        page.goto(f"{copilot_server.base_url}/copilot")
        page.locator("#new-conv-btn").click()
        page.wait_for_url("**/copilot/*")
        own_url = page.url
        expect(page.locator(".rail-item")).to_have_count(2)  # landing conv + the new one

        other_ctx = page.context.browser.new_context()  # type: ignore[union-attr]
        try:
            other = other_ctx.new_page()
            other.goto(f"{copilot_server.base_url}/copilot")
            expect(other.locator(".rail-item")).to_have_count(1)  # only its own fresh one
            assert own_url.rsplit("/", 1)[-1] not in other.content()
            resp = other.goto(own_url)
            assert resp is not None and resp.status == 404
        finally:
            other_ctx.close()
