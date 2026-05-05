"""HTTP Basic auth middleware for production deploys.

Behavior depends on whether `Settings.waggle_user` AND `Settings.waggle_pass`
are both set (typically only in production via env vars / Fly secrets):

* **Both set** → every request requires HTTP Basic credentials matching the
  configured pair. Public-by-design paths (health probes, static assets)
  bypass the check so external monitors and CSS/SVG loads still work.
* **Either unset** → middleware is a no-op; local dev runs without auth.
  This is the existing local experience.

Comparisons use `secrets.compare_digest` to be timing-safe.

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
from starlette.responses import Response

from app.config import get_settings

UNAUTHENTICATED_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/ready",
    "/static",
)

_REALM = 'Basic realm="Waggle", charset="UTF-8"'


def _is_public_path(path: str) -> bool:
    """Return True for paths that must remain accessible without credentials."""
    return any(path == p or path.startswith(p + "/") for p in UNAUTHENTICATED_PATH_PREFIXES)


def _unauthorized() -> Response:
    """Return a 401 response that prompts the browser's basic-auth dialog."""
    return Response(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": _REALM},
    )


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
            return _unauthorized()

        try:
            decoded = b64decode(header[len("Basic ") :].strip()).decode("utf-8")
        except Exception:
            return _unauthorized()

        if ":" not in decoded:
            return _unauthorized()
        user, _, pwd = decoded.partition(":")

        ok_user = secrets.compare_digest(user, settings.waggle_user)
        ok_pwd = secrets.compare_digest(pwd, settings.waggle_pass)
        if not (ok_user and ok_pwd):
            return _unauthorized()

        return await call_next(request)
