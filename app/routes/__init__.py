"""FastAPI route modules — mounted in `app.main.create_app`.

One router per resource. `pages.py` serves Jinja/HTMX HTML; the rest are
JSON APIs.
"""
from __future__ import annotations

from app.routes import agent, customers, health, orders, pages, search, customer_reports

__all__ = [
    "agent",
    "customers",
    "health",
    "orders",
    "pages",
    "search",
    "customer_reports",
]
