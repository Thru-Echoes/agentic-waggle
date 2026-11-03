"""HTTP Basic auth middleware for production deploys.

Behavior depends on whether `Settings.waggle_user` AND `Settings.waggle_pass`
are both set (typically only in production via env vars / Fly secrets):

* **Both set** → every request requires HTTP Basic credentials matching the
  configured pair. Public-by-design paths (health probes, static assets)
  bypass the check so external monitors and CSS/SVG loads still work.
* **Either unset** → middleware is a no-op; local dev runs without auth.

Comparisons use `secrets.compare_digest` on UTF-8 bytes so a username or
password containing non-ASCII characters is rejected with 401 instead of
raising (``compare_digest`` on ``str`` only accepts ASCII).

A browser that cancels the credential dialog is shown an HTML page that
explains what the demo is, states that the data is synthetic, and links to
the portfolio. When ``Settings.waggle_demo_credentials_public`` is set the page
also prints the credentials; that flag exists for the portfolio demo, whose
credentials are published deliberately, and is off by default.

Public surface:

    UNAUTHENTICATED_PATH_PREFIXES: tuple[str, ...]
    class BasicAuthMiddleware(BaseHTTPMiddleware): ...

Side effects: none at import time. The middleware reads `Settings` per
request via the cached `get_settings()` accessor.
"""
from __future__ import annotations

import secrets
from base64 import b64decode
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from app.config import Settings, get_settings
from app.templating import get_templates

UNAUTHENTICATED_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/ready",
    "/static",
)

_REALM = 'Basic realm="Waggle", charset="UTF-8"'


def _is_public_path(path: str) -> bool:
    """Return True for paths that must remain accessible without credentials."""
    return any(path == p or path.startswith(p + "/") for p in UNAUTHENTICATED_PATH_PREFIXES)


def _wants_html(request: Request) -> bool:
    """True when the client is a browser navigating to a page."""
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def _unauthorized(request: Request, settings: Settings) -> Response:
    """Return a 401 that prompts the browser's Basic dialog.

    Browsers render the body only after the dialog is dismissed, so the HTML
    variant is what a reviewer who cancelled sees instead of a bare
    "Unauthorized".
    """
    headers = {"WWW-Authenticate": _REALM}
    if not _wants_html(request):
        return Response("Unauthorized", status_code=401, headers=headers)
    html = get_templates().get_template("401.html").render(
        product=settings.product_name,
        tagline=settings.tagline,
        company=settings.company_name,
        portfolio_url=settings.waggle_portfolio_url,
        demo_user=settings.waggle_user if settings.waggle_demo_credentials_public else None,
        demo_pass=settings.waggle_pass if settings.waggle_demo_credentials_public else None,
        csp_nonce=getattr(request.state, "csp_nonce", ""),
    )
    return HTMLResponse(html, status_code=401, headers=headers)


def _credentials_match(user: str, pwd: str, settings: Settings) -> bool:
    """Timing-safe comparison of the presented pair against the configured one."""
    expected_user = (settings.waggle_user or "").encode("utf-8")
    expected_pass = (settings.waggle_pass or "").encode("utf-8")
    ok_user = secrets.compare_digest(user.encode("utf-8"), expected_user)
    ok_pwd = secrets.compare_digest(pwd.encode("utf-8"), expected_pass)
    return ok_user and ok_pwd


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Gate the app behind HTTP Basic auth when credentials are configured.

    Side effects: per-request, may short-circuit with a 401 response
    instead of dispatching to the rest of the stack.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()
        # Auth is opt-in: only enforced when both halves of the credential
        # pair are configured. Local dev runs without these set.
        if not (settings.waggle_user and settings.waggle_pass):
            return await call_next(request)

        if _is_public_path(request.url.path):
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return _unauthorized(request, settings)

        try:
            decoded = b64decode(header[len("Basic ") :].strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return _unauthorized(request, settings)

        if ":" not in decoded:
            return _unauthorized(request, settings)
        user, _, pwd = decoded.partition(":")

        if not _credentials_match(user, pwd, settings):
            return _unauthorized(request, settings)

        return await call_next(request)
