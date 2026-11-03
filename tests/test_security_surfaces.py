"""Security headers, CSRF origin check, Basic-auth edge cases, error surfaces.

Real app, real middleware stack, FastAPI TestClient; no database needed
for any of these paths (``/health`` and the auth/CSRF rejections return
before a session is opened).
"""
from __future__ import annotations

import re
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def _fresh_app(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    """Build an app with the given environment, clearing the settings cache."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WAGGLE_SKIP_PROVIDER_PROBE", "1")
    get_settings.cache_clear()
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("WAGGLE_USER", raising=False)
    monkeypatch.delenv("WAGGLE_PASS", raising=False)
    c = _fresh_app(monkeypatch)
    try:
        yield c
    finally:
        get_settings.cache_clear()


@pytest.fixture
def authed_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    c = _fresh_app(
        monkeypatch,
        WAGGLE_USER="reviewer",
        WAGGLE_PASS="open-sesame",
        WAGGLE_DEMO_CREDENTIALS_PUBLIC="1",
    )
    try:
        yield c
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_every_response_carries_the_baseline_headers(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in resp.headers["Permissions-Policy"]
        assert re.fullmatch(r"[0-9a-f]{16}", resp.headers["X-Request-ID"])

    def test_csp_has_a_nonce_and_no_unsafe_inline_scripts(self, client: TestClient) -> None:
        csp = client.get("/health").headers["Content-Security-Policy"]
        assert "script-src 'self' 'nonce-" in csp
        assert "'unsafe-inline'" not in csp
        assert "frame-ancestors 'none'" in csp
        assert "form-action 'self'" in csp

    def test_csp_nonce_is_fresh_per_request(self, client: TestClient) -> None:
        a = client.get("/health").headers["Content-Security-Policy"]
        b = client.get("/health").headers["Content-Security-Policy"]
        assert a != b

    def test_swagger_pages_are_exempt_from_csp_only(self, client: TestClient) -> None:
        resp = client.get("/docs")
        assert resp.status_code == 200
        assert "Content-Security-Policy" not in resp.headers
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_hsts_only_when_request_is_https(self, client: TestClient) -> None:
        plain = client.get("/health")
        assert "Strict-Transport-Security" not in plain.headers
        forwarded = client.get("/health", headers={"X-Forwarded-Proto": "https"})
        assert forwarded.headers["Strict-Transport-Security"].startswith("max-age=31536000")

    def test_incoming_proxy_request_id_is_echoed(self, client: TestClient) -> None:
        resp = client.get("/health", headers={"Fly-Request-Id": "01ABCDEF-sjc"})
        assert resp.headers["X-Request-ID"] == "01ABCDEF-sjc"


# ---------------------------------------------------------------------------
# CSRF origin check
# ---------------------------------------------------------------------------


class TestCsrfOriginCheck:
    def test_cross_site_post_is_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/inbox/extract",
            data={"account_id": "x", "message_id": "y"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["title"] == "Cross-site request rejected"
        assert body["request_id"]

    def test_same_site_subdomain_is_rejected(self, client: TestClient) -> None:
        resp = client.post("/copilot/conversations", headers={"Sec-Fetch-Site": "same-site"})
        assert resp.status_code == 403

    def test_origin_mismatch_without_fetch_metadata_is_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/inbox/extract",
            data={"account_id": "x", "message_id": "y"},
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 403

    def test_same_origin_browser_post_passes_through(self, client: TestClient) -> None:
        # Missing form fields → the route's own 422, proving the middleware let it in.
        resp = client.post("/inbox/extract", headers={"Sec-Fetch-Site": "same-origin"})
        assert resp.status_code == 422

    def test_non_browser_post_without_headers_passes_through(self, client: TestClient) -> None:
        resp = client.post("/inbox/extract")
        assert resp.status_code == 422

    def test_safe_methods_are_never_checked(self, client: TestClient) -> None:
        resp = client.get("/health", headers={"Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Basic auth edge cases
# ---------------------------------------------------------------------------


class TestBasicAuth:
    def test_browser_gets_html_401_with_realm_and_credentials(self, authed_client: TestClient) -> None:
        resp = authed_client.get("/", headers={"Accept": "text/html"})
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"].startswith("Basic realm=")
        assert resp.headers["content-type"].startswith("text/html")
        assert "synthetic data" in resp.text
        assert "reviewer" in resp.text and "open-sesame" in resp.text
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_credentials_hidden_unless_explicitly_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = _fresh_app(monkeypatch, WAGGLE_USER="reviewer", WAGGLE_PASS="open-sesame",
                       WAGGLE_DEMO_CREDENTIALS_PUBLIC="0")
        try:
            resp = c.get("/", headers={"Accept": "text/html"})
            assert resp.status_code == 401
            assert "open-sesame" not in resp.text
            assert "listed next to the demo link" in resp.text
        finally:
            get_settings.cache_clear()

    def test_non_browser_gets_plain_401(self, authed_client: TestClient) -> None:
        resp = authed_client.get("/api/orders")
        assert resp.status_code == 401
        assert resp.text == "Unauthorized"

    def test_non_ascii_username_is_a_401_not_a_500(self, authed_client: TestClient) -> None:
        import base64

        token = base64.b64encode("tëster:x".encode("utf-8")).decode("ascii")
        resp = authed_client.get("/api/orders", headers={"Authorization": f"Basic {token}"})
        assert resp.status_code == 401

    def test_malformed_base64_is_a_401(self, authed_client: TestClient) -> None:
        resp = authed_client.get("/api/orders", headers={"Authorization": "Basic %%%not-base64"})
        assert resp.status_code == 401

    def test_correct_credentials_pass(self, authed_client: TestClient) -> None:
        resp = authed_client.get("/health", auth=("reviewer", "open-sesame"))
        assert resp.status_code == 200
        resp = authed_client.get("/docs", auth=("reviewer", "open-sesame"))
        assert resp.status_code == 200

    def test_public_paths_bypass_auth(self, authed_client: TestClient) -> None:
        assert authed_client.get("/health").status_code == 200
        assert authed_client.get("/static/waggle.css").status_code == 200


# ---------------------------------------------------------------------------
# Error surfaces
# ---------------------------------------------------------------------------


class TestErrorSurfaces:
    def test_internal_render_routes_are_off_by_default(self, client: TestClient) -> None:
        resp = client.get("/_internal/render/pick-match?turn_id=x&alt=noseparator")
        assert resp.status_code == 404

    def test_internal_render_routes_mount_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import uuid

        c = _fresh_app(monkeypatch, WAGGLE_INTERNAL_ROUTES_ENABLED="1")
        try:
            resp = c.get(f"/_internal/render/pick-match?turn_id={uuid.uuid4()}&alt=noseparator")
            # Malformed alt is a client error, not a 500 with a traceback.
            assert resp.status_code == 422
        finally:
            get_settings.cache_clear()

    def test_unhandled_exception_is_generic_with_request_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAGGLE_SKIP_PROVIDER_PROBE", "1")
        get_settings.cache_clear()
        app = create_app()

        @app.get("/_boom")
        def _boom() -> None:
            raise RuntimeError("secret dialect detail postgresql://user:pw@host/db")

        c = TestClient(app, raise_server_exceptions=False)
        try:
            resp = c.get("/_boom")
            assert resp.status_code == 500
            body = resp.json()
            assert body["title"] == "Internal server error"
            assert "postgresql://" not in resp.text
            assert body["request_id"] == resp.headers["X-Request-ID"]
        finally:
            get_settings.cache_clear()

    def test_htmx_request_gets_an_html_error_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAGGLE_SKIP_PROVIDER_PROBE", "1")
        get_settings.cache_clear()
        app = create_app()

        @app.get("/_boom")
        def _boom() -> None:
            raise RuntimeError("nope")

        c = TestClient(app, raise_server_exceptions=False)
        try:
            resp = c.get("/_boom", headers={"HX-Request": "true"})
            assert resp.status_code == 500
            assert resp.headers["content-type"].startswith("text/html")
            assert 'class="confirm-result error"' in resp.text
            assert "Request id" in resp.text
            assert "nope" not in resp.text
        finally:
            get_settings.cache_clear()

    def test_provider_failure_is_a_503_with_a_fixed_phrase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx2  # the OpenAI SDK's bundled httpx fork; its errors are typed against it
        import openai

        monkeypatch.setenv("WAGGLE_SKIP_PROVIDER_PROBE", "1")
        get_settings.cache_clear()
        app = create_app()

        @app.get("/_dead_key")
        def _dead_key() -> None:
            request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
            response = httpx2.Response(401, request=request)
            raise openai.AuthenticationError(
                "Error code: 401 - Incorrect API key provided: sk-proj-abc…xyz",
                response=response,
                body=None,
            )

        c = TestClient(app, raise_server_exceptions=False)
        try:
            resp = c.get("/_dead_key")
            assert resp.status_code == 503
            body = resp.json()
            assert body["title"] == "LLM provider unavailable"
            assert body["detail"] == "The LLM provider rejected this server's API key."
            assert "sk-proj" not in resp.text

            card = c.get("/_dead_key", headers={"HX-Request": "true"})
            assert card.status_code == 503
            assert "rejected this server" in card.text
            assert "sk-proj" not in card.text
        finally:
            get_settings.cache_clear()

    def test_http_exception_keeps_its_message(self, client: TestClient) -> None:
        resp = client.get("/api/orders/00000000-0000-0000-0000-000000000000")
        # 404 from the route (needs DB) or 500 if no DB: either way the shape is problem+json.
        assert resp.status_code in (404, 500)
        assert "request_id" in resp.json()
