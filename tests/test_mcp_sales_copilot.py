"""E2E tests for the Sales Copilot MCP server.

Exercises the tools via fastmcp's in-memory `Client` — the same protocol a
real MCP client (Claude Desktop, Anthropic's MCP CLI) would use, minus the
transport hop. DB-only tools run without an LLM key; `draft_followup_email`
skips if no key is set.

Side effects: seeds a minimal DB corpus at the top of each test.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os

import pytest
from fastmcp import Client

from mcp_servers.sales_copilot_server import server


def _seed_copilot_corpus(db, faker_instance) -> None:
    from seed import apiarists as apiarists_gen
    from seed import customers as customers_gen
    from seed import emails as emails_gen
    from seed import orders as orders_gen
    from seed import products as products_gen
    from seed import regions as regions_gen
    from seed import services as services_gen
    from seed import sites as sites_gen

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    products = products_gen.generate(faker_instance)
    products_gen.persist(db, products)
    services = services_gen.generate()
    services_gen.persist(db, services)
    emails = emails_gen.generate(faker_instance, customers, apiarists)
    emails_gen.persist(db, emails)
    orders = orders_gen.generate(
        faker_instance, customers, sites, apiarists, products, services, emails
    )
    orders_gen.persist(db, orders)
    # The MCP tool opens its own `session_scope()`, a separate transaction
    # from the test's `db` fixture. Commit here so the MCP session can see
    # the seeded rows. Without this, tool calls hit LookupError on fresh data.
    db.commit()


async def _first_customer_id(db) -> str:
    from sqlalchemy import text
    return str(
        db.execute(text("SELECT id FROM customers LIMIT 1")).scalar_one()
    )


class TestFindCustomer:
    @pytest.mark.asyncio
    async def test_exact_name_returns_hit(self, db, faker_instance) -> None:
        _seed_copilot_corpus(db, faker_instance)
        async with Client(server) as client:
            result = await client.call_tool(
                "find_customer",
                {"name_or_alias": "O'Reilly Family Orchards"},
            )
        # fastmcp returns structured data in `result.data`.
        hits = result.data
        assert isinstance(hits, list)
        assert len(hits) >= 1

    @pytest.mark.asyncio
    async def test_no_match_returns_empty_list(
        self, db, faker_instance
    ) -> None:
        _seed_copilot_corpus(db, faker_instance)
        async with Client(server) as client:
            result = await client.call_tool(
                "find_customer",
                {"name_or_alias": "Completely Fictional Co"},
            )
        assert result.data == []


class TestListCustomerSites:
    @pytest.mark.asyncio
    async def test_returns_sites_for_known_customer(
        self, db, faker_instance
    ) -> None:
        _seed_copilot_corpus(db, faker_instance)
        customer_id = await _first_customer_id(db)
        async with Client(server) as client:
            result = await client.call_tool(
                "list_customer_sites",
                {"customer_id": customer_id},
            )
        assert isinstance(result.data, list)

    @pytest.mark.asyncio
    async def test_unknown_customer_raises(
        self, db, faker_instance
    ) -> None:
        _seed_copilot_corpus(db, faker_instance)
        async with Client(server) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "list_customer_sites",
                    {"customer_id": "00000000-0000-0000-0000-000000000000"},
                )


class TestListRecentOrders:
    @pytest.mark.asyncio
    async def test_returns_orders_newest_first(
        self, db, faker_instance
    ) -> None:
        _seed_copilot_corpus(db, faker_instance)
        customer_id = await _first_customer_id(db)
        async with Client(server) as client:
            result = await client.call_tool(
                "list_recent_orders",
                {"customer_id": customer_id, "limit": 5},
            )
        assert isinstance(result.data, list)
        # If the customer has orders, they should be in descending date order.
        data = result.data
        if len(data) >= 2:
            # Attribute access — fastmcp wraps tool results as dataclasses.
            assert data[0].created_at >= data[1].created_at


class TestDraftFollowupEmail:
    @pytest.mark.asyncio
    async def test_skips_if_no_llm_key(
        self, db, faker_instance
    ) -> None:
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            pytest.skip(
                "LLM key present — the network-backed test runs only without "
                "keys set so we can hit the loudly-raising path."
            )
        _seed_copilot_corpus(db, faker_instance)
        customer_id = await _first_customer_id(db)
        async with Client(server) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "draft_followup_email",
                    {"customer_id": customer_id, "topic": "sample"},
                )

    @pytest.mark.asyncio
    async def test_real_api_drafts_email(
        self, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_copilot_corpus(db, faker_instance)
        customer_id = await _first_customer_id(db)
        async with Client(server) as client:
            result = await client.call_tool(
                "draft_followup_email",
                {
                    "customer_id": customer_id,
                    "topic": "upcoming pollination window",
                    "tone": "professional",
                },
            )
        data = result.data
        assert data.subject
        assert data.body_text
        assert data.tone == "professional"
