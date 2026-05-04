"""LangGraph agents for Waggle.

Public entrypoints:
    * `agents.order_extractor.run_order_extraction(email)` — ReAct pipeline
      that reads an inbound email and returns a validated `OrderDraft`.

Agent I/O models live in `agents.schemas` so route handlers + MCP tools
can consume them without importing the agent runtime.
"""
from __future__ import annotations

from agents import customer_matcher, order_extractor, schemas, sku_resolver

__all__ = ["customer_matcher", "order_extractor", "schemas", "sku_resolver"]
