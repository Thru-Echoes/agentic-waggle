"""Per-client token bucket and daily budget on the LLM-backed routes.

The limiter runs before routing, so a POST with no body is enough to
exercise it: allowed requests reach the route and get its 422 for missing
fields; rejected ones get a 429 before any handler runs. No database and
no model are touched.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.ratelimit import LlmRateLimiter, is_llm_backed


def _app(monkeypatch: pytest.MonkeyPatch, per_minute: str, daily: str) -> TestClient:
    monkeypatch.delenv("WAGGLE_USER", raising=False)
    monkeypatch.delenv("WAGGLE_PASS", raising=False)
    monkeypatch.setenv("WAGGLE_SKIP_PROVIDER_PROBE", "1")
    monkeypatch.setenv("WAGGLE_LLM_RATE_PER_MINUTE", per_minute)
    monkeypatch.setenv("WAGGLE_LLM_DAILY_BUDGET", daily)
    get_settings.cache_clear()
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    c = _app(monkeypatch, per_minute="2", daily="5")
    try:
        yield c
    finally:
        get_settings.cache_clear()


class TestPathSelection:
    def test_only_unsafe_methods_on_llm_paths_count(self) -> None:
        assert is_llm_backed("POST", "/copilot/turns")
        assert is_llm_backed("POST", "/copilot/turns/abc/confirm-match")
        assert is_llm_backed("POST", "/inbox/extract")
        assert is_llm_backed("POST", "/api/agent/extract")
        assert not is_llm_backed("GET", "/copilot/turns")
        assert not is_llm_backed("POST", "/copilot/conversations")
        assert not is_llm_backed("POST", "/inbox/confirm")
        assert not is_llm_backed("GET", "/inbox/extract")


class TestLimiterUnit:
    def test_bucket_refills_over_time(self) -> None:
        now = [1000.0]
        lim = LlmRateLimiter(per_minute=2, daily_budget=0, clock=lambda: now[0])
        assert lim.check("a")[0] and lim.check("a")[0]
        allowed, reason, retry = lim.check("a")
        assert not allowed and reason == "client" and retry >= 1
        now[0] += 31.0  # half a refill period restores one token
        assert lim.check("a")[0]
        assert not lim.check("a")[0]

    def test_clients_are_independent(self) -> None:
        lim = LlmRateLimiter(per_minute=1, daily_budget=0)
        assert lim.check("a")[0]
        assert not lim.check("a")[0]
        assert lim.check("b")[0]

    def test_daily_budget_counts_across_clients(self) -> None:
        lim = LlmRateLimiter(per_minute=0, daily_budget=3)
        assert lim.remaining_today() == 3
        for ip in ("a", "b", "c"):
            assert lim.check(ip)[0]
        allowed, reason, retry = lim.check("d")
        assert not allowed and reason == "daily" and retry >= 1
        assert lim.remaining_today() == 0

    def test_disabled_checks_always_allow(self) -> None:
        lim = LlmRateLimiter(per_minute=0, daily_budget=0)
        assert all(lim.check("a")[0] for _ in range(50))
        assert lim.remaining_today() is None


class TestMiddleware:
    def test_third_request_in_a_minute_is_429_with_retry_after(self, client: TestClient) -> None:
        first = client.post("/inbox/extract", headers={"Fly-Client-IP": "203.0.113.7"})
        second = client.post("/inbox/extract", headers={"Fly-Client-IP": "203.0.113.7"})
        assert first.status_code == 422 and second.status_code == 422  # reached the route
        third = client.post("/inbox/extract", headers={"Fly-Client-IP": "203.0.113.7"})
        assert third.status_code == 429
        assert int(third.headers["Retry-After"]) >= 1
        body = third.json()
        assert body["title"] == "Rate limit reached"
        assert "this address" in body["detail"]
        assert body["request_id"]

    def test_other_clients_are_not_affected(self, client: TestClient) -> None:
        for _ in range(3):
            client.post("/inbox/extract", headers={"Fly-Client-IP": "203.0.113.7"})
        other = client.post("/inbox/extract", headers={"Fly-Client-IP": "203.0.113.8"})
        assert other.status_code == 422

    def test_htmx_gets_a_card(self, client: TestClient) -> None:
        for _ in range(2):
            client.post("/inbox/extract", headers={"Fly-Client-IP": "203.0.113.9"})
        resp = client.post(
            "/inbox/extract",
            headers={"Fly-Client-IP": "203.0.113.9", "HX-Request": "true"},
        )
        assert resp.status_code == 429
        assert resp.headers["content-type"].startswith("text/html")
        assert "Rate limit reached" in resp.text
        assert "not limited" in resp.text

    def test_daily_budget_rejects_everyone(self, client: TestClient) -> None:
        # budget 5; spread over distinct clients so the per-client bucket never trips
        for i in range(5):
            r = client.post("/inbox/extract", headers={"Fly-Client-IP": f"198.51.100.{i}"})
            assert r.status_code == 422
        r = client.post("/inbox/extract", headers={"Fly-Client-IP": "198.51.100.99"})
        assert r.status_code == 429
        assert "daily budget" in r.json()["detail"]

    def test_non_llm_routes_are_never_limited(self, client: TestClient) -> None:
        for _ in range(10):
            assert client.get("/health", headers={"Fly-Client-IP": "203.0.113.1"}).status_code == 200

    def test_limiter_can_be_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = _app(monkeypatch, per_minute="0", daily="0")
        try:
            for _ in range(10):
                assert c.post("/inbox/extract", headers={"Fly-Client-IP": "203.0.113.2"}).status_code == 422
        finally:
            get_settings.cache_clear()
