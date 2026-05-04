"""E2E tests for the Trip Reports MCP server."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from fastmcp import Client

from mcp_servers.customer_reports_server import server


def _seed_trip_corpus(db, faker_instance) -> None:
    from seed import apiarists as apiarists_gen
    from seed import customers as customers_gen
    from seed import inspections as inspections_gen
    from seed import regions as regions_gen
    from seed import sites as sites_gen

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    inspections = inspections_gen.generate(faker_instance, sites, apiarists)
    inspections_gen.persist(db, inspections)
    # MCP tools open their own session; commit so they see the seeded rows.
    db.commit()


class TestQueryByDisease:
    @pytest.mark.asyncio
    async def test_unknown_disease_raises(
        self, db, faker_instance
    ) -> None:
        _seed_trip_corpus(db, faker_instance)
        async with Client(server) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "query_by_disease",
                    {"disease_code": "BOGUS"},
                )

    @pytest.mark.asyncio
    async def test_known_disease_returns_list(
        self, db, faker_instance
    ) -> None:
        _seed_trip_corpus(db, faker_instance)
        async with Client(server) as client:
            result = await client.call_tool(
                "query_by_disease",
                {"disease_code": "VARROA"}
                if False
                else {"disease_code": "AFB"},
            )
        assert isinstance(result.data, list)


class TestQueryByVarroaThreshold:
    @pytest.mark.asyncio
    async def test_negative_threshold_raises(
        self, db, faker_instance
    ) -> None:
        _seed_trip_corpus(db, faker_instance)
        async with Client(server) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "query_by_varroa_threshold",
                    {"min_count": -1.0},
                )

    @pytest.mark.asyncio
    async def test_zero_returns_all_reports(
        self, db, faker_instance
    ) -> None:
        _seed_trip_corpus(db, faker_instance)
        async with Client(server) as client:
            result = await client.call_tool(
                "query_by_varroa_threshold",
                {"min_count": 0.0, "limit": 100},
            )
        data = result.data
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_high_threshold_returns_fewer(
        self, db, faker_instance
    ) -> None:
        _seed_trip_corpus(db, faker_instance)
        async with Client(server) as client:
            low = await client.call_tool(
                "query_by_varroa_threshold",
                {"min_count": 0.0, "limit": 100},
            )
            high = await client.call_tool(
                "query_by_varroa_threshold",
                {"min_count": 5.0, "limit": 100},
            )
        assert len(high.data) <= len(low.data)


class TestAggregateFleetHealth:
    @pytest.mark.asyncio
    async def test_fleet_scope_returns_summary(
        self, db, faker_instance
    ) -> None:
        _seed_trip_corpus(db, faker_instance)
        async with Client(server) as client:
            result = await client.call_tool(
                "aggregate_fleet_health",
                {},
            )
        # fastmcp wraps structured (single-object) returns as a dataclass
        # — use attribute access, not subscript. List-returning tools give
        # a list where items are also attribute-accessible.
        data = result.data
        assert data.scope == "fleet"
        assert data.reports_considered > 0
        assert float(data.max_varroa) >= float(data.avg_varroa)

    @pytest.mark.asyncio
    async def test_unknown_region_raises(
        self, db, faker_instance
    ) -> None:
        _seed_trip_corpus(db, faker_instance)
        async with Client(server) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "aggregate_fleet_health",
                    {"region_code": "atlantis"},
                )


class TestExtractFromNarrative:
    @pytest.mark.asyncio
    async def test_empty_narrative_raises(self) -> None:
        async with Client(server) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "extract_from_narrative",
                    {"narrative": "   "},
                )

    @pytest.mark.asyncio
    async def test_real_extraction(self, require_llm_api_key) -> None:
        narrative = (
            "Visited the Martinez site on Tuesday. Placed 120 hives across "
            "the back half of the orchard. Varroa mite count ran about 2.5 "
            "per 100 bees — on the edge of treatment threshold but not over. "
            "Saw some chalkbrood but no AFB or EFB."
        )
        async with Client(server) as client:
            result = await client.call_tool(
                "extract_from_narrative",
                {"narrative": narrative},
            )
        data = result.data
        assert data.hives_placed == 120
        assert 2.0 <= float(data.varroa_count) <= 3.0
        assert 0.0 <= float(data.confidence) <= 1.0
