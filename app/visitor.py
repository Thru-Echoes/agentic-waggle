"""Visitor identity for the shared-login demo.

Every reviewer signs in with the same Basic-auth pair, so the app needs
something else to tell browsers apart: a random visitor id in a signed,
HttpOnly cookie. Conversations record the visitor that created them and the
copilot rail lists only that visitor's conversations.

The signature is an HMAC over the id with ``Settings.waggle_secret_key``; a
tampered or foreign cookie is simply replaced by a fresh id. Without a
configured key a random per-process key is used, which means visitors are
forgotten when the process restarts (acceptable for local use; set the key
in production).

Public surface:

    COOKIE_NAME: str
    VisitorIdMiddleware(BaseHTTPMiddleware)
    get_visitor_id(request) -> str          (reads request.state)

Side effects: the middleware sets ``request.state.visitor_id`` on every
request and adds a ``Set-Cookie`` header when it minted a new id.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings

COOKIE_NAME = "waggle_visitor"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 90

_PROCESS_KEY = secrets.token_bytes(32)


def _key() -> bytes:
    configured = get_settings().waggle_secret_key
    return configured.encode("utf-8") if configured else _PROCESS_KEY


def _sign(visitor_id: str) -> str:
    return hmac.new(_key(), visitor_id.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def encode_cookie(visitor_id: str) -> str:
    """Return ``<id>.<signature>`` for ``visitor_id``. Side effects: none."""
    return f"{visitor_id}.{_sign(visitor_id)}"


def decode_cookie(value: str | None) -> str | None:
    """Return the visitor id when ``value`` carries a valid signature, else None."""
    if not value or "." not in value:
        return None
    visitor_id, _, signature = value.partition(".")
    try:
        uuid.UUID(visitor_id)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(visitor_id)):
        return None
    return visitor_id


def get_visitor_id(request: Request) -> str:
    """Return the visitor id the middleware attached to this request.

    Raises ``RuntimeError`` if the middleware is not installed: scoping
    without an id would silently expose every conversation.
    """
    visitor_id = getattr(request.state, "visitor_id", None)
    if not visitor_id:
        raise RuntimeError(
            "request.state.visitor_id is missing; VisitorIdMiddleware must be "
            "registered in app.main.create_app"
        )
    return visitor_id


class VisitorIdMiddleware(BaseHTTPMiddleware):
    """Attach a signed visitor id to every request, minting one when absent."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        visitor_id = decode_cookie(request.cookies.get(COOKIE_NAME))
        minted = visitor_id is None
        if visitor_id is None:
            visitor_id = str(uuid.uuid4())
        request.state.visitor_id = visitor_id
        response = await call_next(request)
        # The JSON API is the programmatic surface: a cookie-less client
        # (curl, scripts) must keep its API-only view across calls, so no
        # cookie is minted there. Browsers that already hold one send it.
        if minted and not request.url.path.startswith("/api/"):
            response.set_cookie(
                COOKIE_NAME,
                encode_cookie(visitor_id),
                max_age=COOKIE_MAX_AGE_SECONDS,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto", "").startswith("https"),
                path="/",
            )
        return response


__all__ = [
    "COOKIE_NAME",
    "VisitorIdMiddleware",
    "decode_cookie",
    "encode_cookie",
    "get_visitor_id",
]
