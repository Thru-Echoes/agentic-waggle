"""Rate limiting for the LLM-backed routes.

The demo's credentials are public by design, so the limiter is what stands
between the internet and the model bill. Two independent checks, both
in-memory and therefore per process:

* a per-client token bucket (``per_minute`` requests, same burst) keyed by
  the client address Fly supplies (``Fly-Client-IP``) or the socket peer;
* a process-wide daily budget of LLM-backed requests, reset at 00:00 UTC.

Only unsafe requests to the LLM-backed paths are counted; everything else
passes untouched. A rejected request gets a 429 problem detail (or an HTML
card for HTMX) with ``Retry-After``. The web app runs as one machine, so a
process-local limiter is exact; the Redis dependency already in the lock
is the upgrade path if that changes.

Side effects: none at import time. The limiter mutates its own in-memory
state per request.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.errors import problem_response

#: POST to any path with one of these prefixes triggers at least one LLM call.
LLM_BACKED_PATH_PREFIXES: tuple[str, ...] = (
    "/copilot/turns",
    "/inbox/extract",
    "/api/agent/extract",
)

UNSAFE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_llm_backed(method: str, path: str) -> bool:
    """True for unsafe-method requests to a path that will call the model."""
    return method in UNSAFE_METHODS and path.startswith(LLM_BACKED_PATH_PREFIXES)


def get_client_ip(request: Request) -> str:
    """Return the client address Fly's proxy attests, else the socket peer.

    ``X-Forwarded-For`` is deliberately not consulted: a client can prepend
    arbitrary values to it, whereas ``Fly-Client-IP`` is set by the edge.
    """
    fly = request.headers.get("fly-client-ip")
    if fly:
        return fly.strip()
    return request.client.host if request.client else "unknown"


class LlmRateLimiter:
    """Token buckets per client plus a daily budget. Thread-safe.

    ``per_minute`` or ``daily_budget`` of 0 disables that check.
    """

    def __init__(self, *, per_minute: int, daily_budget: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.daily_budget = daily_budget
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_refill)
        self._day: str = self._today()
        self._used_today: int = 0

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._used_today = 0

    def remaining_today(self) -> int | None:
        """Requests left in today's budget, or None when the budget is disabled."""
        with self._lock:
            self._roll_day()
            if self.daily_budget <= 0:
                return None
            return max(self.daily_budget - self._used_today, 0)

    def check(self, client_ip: str) -> tuple[bool, str, int]:
        """Consume one request for ``client_ip``.

        Returns ``(allowed, reason, retry_after_seconds)``. ``reason`` is
        ``"ok"``, ``"client"`` (per-client bucket empty) or ``"daily"``
        (budget exhausted). Side effects: mutates buckets and the counter.
        """
        with self._lock:
            self._roll_day()
            if self.daily_budget > 0 and self._used_today >= self.daily_budget:
                return False, "daily", self._seconds_until_midnight_utc()
            if self.per_minute > 0:
                now = self._clock()
                tokens, last = self._buckets.get(client_ip, (float(self.per_minute), now))
                refill_rate = self.per_minute / 60.0
                tokens = min(float(self.per_minute), tokens + (now - last) * refill_rate)
                if tokens < 1.0:
                    self._buckets[client_ip] = (tokens, now)
                    return False, "client", int(math.ceil((1.0 - tokens) / refill_rate))
                self._buckets[client_ip] = (tokens - 1.0, now)
                self._prune(now)
            self._used_today += 1
            return True, "ok", 0

    def _prune(self, now: float) -> None:
        """Drop buckets idle for longer than one full refill (bounds memory)."""
        if len(self._buckets) < 1024:
            return
        idle = 60.0
        self._buckets = {ip: v for ip, v in self._buckets.items() if now - v[1] < idle}

    @staticmethod
    def _seconds_until_midnight_utc() -> int:
        now = datetime.now(timezone.utc)
        tomorrow = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                    + __import__("datetime").timedelta(days=1))
        return max(int((tomorrow - now).total_seconds()), 1)


class LlmRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply ``LlmRateLimiter`` to the LLM-backed POST routes."""

    def __init__(self, app, limiter: LlmRateLimiter) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not is_llm_backed(request.method, request.url.path):
            return await call_next(request)
        allowed, reason, retry_after = self.limiter.check(get_client_ip(request))
        if allowed:
            return await call_next(request)
        if reason == "daily":
            detail = (
                "This demo's daily budget of model-backed requests is used up. "
                "It resets at 00:00 UTC."
            )
        else:
            detail = (
                f"Too many model-backed requests from this address. "
                f"Try again in about {retry_after} seconds."
            )
        response = problem_response(
            request,
            status=429,
            title="Rate limit reached",
            detail=detail,
            hint="Browsing customers, orders and reports is not limited.",
        )
        response.headers["Retry-After"] = str(retry_after)
        return response


__all__ = [
    "LLM_BACKED_PATH_PREFIXES",
    "LlmRateLimitMiddleware",
    "LlmRateLimiter",
    "get_client_ip",
    "is_llm_backed",
]
