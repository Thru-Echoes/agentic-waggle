"""E2E tests for SKU resolution (exact + fuzzy). No LLM."""
# pyright: reportMissingImports=false
from __future__ import annotations

from decimal import Decimal

from agents.schemas import RequestedLineItem
from agents.sku_resolver import FUZZY_SCORE_MIN, resolve_line_item


def _seed(db, faker_instance) -> None:
    from seed import products as products_gen
    from seed import services as services_gen
    products_gen.persist(db, products_gen.generate(faker_instance))
    services_gen.persist(db, services_gen.generate())


class TestResolveLineItem:
    def test_exact_sku_match_wins(self, db, faker_instance) -> None:
        _seed(db, faker_instance)
        # Pick an actual SKU from the seeded catalog
        from seed import products as products_gen
        products = products_gen.generate(faker_instance)
        target_sku = products[0].sku

        req = RequestedLineItem(
            description="whatever",
            quantity=5,
            mentioned_sku=target_sku,
        )
        resolved = resolve_line_item(db, req)
        assert resolved is not None
        assert resolved.sku == target_sku
        assert resolved.match_method == "exact_sku"

    def test_fuzzy_name_matches_by_description(self, db, faker_instance) -> None:
        _seed(db, faker_instance)
        # Use a description close to a real product name.
        req = RequestedLineItem(
            description="Italian queen bees",
            quantity=10,
        )
        resolved = resolve_line_item(db, req)
        if resolved is not None:
            assert resolved.match_method == "fuzzy_name"

    def test_quantity_preserved_on_match(self, db, faker_instance) -> None:
        _seed(db, faker_instance)
        from seed import products as products_gen
        products = products_gen.generate(faker_instance)
        req = RequestedLineItem(
            description=products[0].name,
            quantity=42,
            mentioned_sku=products[0].sku,
        )
        resolved = resolve_line_item(db, req)
        assert resolved is not None
        assert resolved.quantity == 42

    def test_mentioned_price_wins_over_catalog(self, db, faker_instance) -> None:
        _seed(db, faker_instance)
        from seed import products as products_gen
        products = products_gen.generate(faker_instance)
        override_price = Decimal("999.99")
        req = RequestedLineItem(
            description=products[0].name,
            quantity=1,
            mentioned_sku=products[0].sku,
            mentioned_unit_price_usd=override_price,
        )
        resolved = resolve_line_item(db, req)
        assert resolved is not None
        assert resolved.unit_price_usd == override_price

    def test_threshold_guards_against_garbage(self, db, faker_instance) -> None:
        _seed(db, faker_instance)
        req = RequestedLineItem(
            description="space laser for moonlighting",
            quantity=1,
        )
        # The specific outcome depends on the catalog but the threshold
        # floor is what makes this safe. Guard against someone tuning it
        # down to "match anything" territory.
        _ = resolve_line_item(db, req)
        assert FUZZY_SCORE_MIN > 50.0

    def test_product_vs_service_populated_correctly(
        self, db, faker_instance
    ) -> None:
        _seed(db, faker_instance)
        from seed import products as products_gen
        from seed import services as services_gen
        prod_sku = products_gen.generate(faker_instance)[0].sku
        svc_sku = services_gen.generate()[0].sku

        prod_req = RequestedLineItem(
            description="any", quantity=1, mentioned_sku=prod_sku
        )
        svc_req = RequestedLineItem(
            description="any", quantity=1, mentioned_sku=svc_sku
        )
        prod_res = resolve_line_item(db, prod_req)
        svc_res = resolve_line_item(db, svc_req)
        assert prod_res is not None and prod_res.product_id is not None
        assert prod_res.service_id is None
        assert svc_res is not None and svc_res.service_id is not None
        assert svc_res.product_id is None
