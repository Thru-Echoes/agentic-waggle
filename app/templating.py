"""One Jinja environment for every HTML route.

``get_templates()`` returns a cached ``Jinja2Templates`` whose context
processor injects the per-request CSP nonce (set by
``app.middleware.RequestContextMiddleware``) and request id, so templates
can tag their ``<script>`` elements with ``nonce="{{ csp_nonce }}"`` and
error pages can show the id a reader should quote.

Side effects: none beyond constructing the Jinja environment once.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _request_context(request: Request) -> dict[str, Any]:
    """Context processor: values every template may need from the request."""
    return {
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
        "request_id": getattr(request.state, "request_id", ""),
    }


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    """Return the process-wide template environment (cached)."""
    return Jinja2Templates(
        directory=str(TEMPLATE_DIR),
        context_processors=[_request_context],
    )


__all__ = ["TEMPLATE_DIR", "get_templates"]
