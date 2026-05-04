"""E2E tests for the order generator."""
# pyright: reportMissingImports=false
from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import text

from seed import apiarists as apiarists_gen
from seed import customers as customers_gen
from seed import emails as emails_gen
from seed import orders as orders_gen
from seed import products as products_gen
from seed import regions as regions_gen
from seed import services as services_gen
from seed import sites as sites_gen


def _build_inputs(faker_instance):
    regions = regions_gen.generate()
    customers = customers_gen.generate(faker_instance)
    sites = sites_gen.generate(faker_instance, customers, regions)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    products = products_gen.generate(faker_instance)
    services = services_gen.generate()
    emails = emails_gen.generate(faker_instance, customers, apiarists)
    return regions, customers, sites, apiarists, products, services, emails


class TestGenerate:
    def test_exactly_one_hundred_fifty_orders(self, faker_instance) -> None:
        args = _build_inputs(faker_instance)
        orders = orders_gen.generate(faker_instance, *args[1:])
        assert len(orders) == 150

    def test_all_fsm_states_represented(self, faker_instance) -> None:
        args = _build_inputs(faker_instance)
        orders = orders_gen.generate(faker_instance, *args[1:])
        states = {o.state for o in orders}
        expected = {s for s, _ in orders_gen.STATE_DISTRIBUTION}
        assert states == expected

    def test_state_counts_match_distribution(self, faker_instance) -> None:
        args = _build_inputs(faker_instance)
        orders = orders_gen.generate(faker_instance, *args[1:])
        by_state = Counter(o.state for o in orders)
        for state, count in orders_gen.STATE_DISTRIBUTION:
            assert by_state[state] == count, f"{state}: got {by_state[state]}, want {count}"

    def test_totals_equal_line_item_sums(self, faker_instance) -> None:
        from decimal import Decimal
        args = _build_inputs(faker_instance)
        orders = orders_gen.generate(faker_instance, *args[1:])
        for o in orders:
            computed = sum(
                (li.unit_price_usd * li.quantity for li in o.line_items),
                start=Decimal("0"),
            )
            assert o.total_usd == computed.quantize(o.total_usd)

    def test_email_consuming_states_have_source_email_ref(self, faker_instance) -> None:
        """Covers both directions: consuming states MUST have a ref; other
        states MUST NOT (silent leakage in either direction would be a bug)."""
        args = _build_inputs(faker_instance)
        orders = orders_gen.generate(faker_instance, *args[1:])
        consuming = {"EXTRACTED", "UNDER_REVIEW", "APPROVED", "SCHEDULED"}
        for o in orders:
            if o.state in consuming:
                assert o.source_email_ref is not None, (
                    f"Order in state {o.state!r} should reference a source email"
                )
            else:
                assert o.source_email_ref is None, (
                    f"Order in non-consuming state {o.state!r} should NOT "
                    f"have a source email ref; got {o.source_email_ref!r}"
                )

    def test_insufficient_emails_raises_value_error(self, faker_instance) -> None:
        args = _build_inputs(faker_instance)
        _, customers, sites, apiarists, products, services, full_emails = args
        # Only pass 5 emails — not enough for the email-consuming states.
        truncated = full_emails[:5]
        with pytest.raises(ValueError):
            orders_gen.generate(
                faker_instance, customers, sites, apiarists, products, services, truncated
            )


class TestPersist:
    def _setup(self, db, faker_instance, inbox_fixture_root):
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
        import seed.emails as emails_mod
        emails_mod.GMAIL_FIXTURE_ROOT = inbox_fixture_root
        emails = emails_gen.generate(faker_instance, customers, apiarists)
        emails_gen.persist(db, emails)
        return customers, sites, apiarists, products, services, emails

    def test_inserts_orders_and_line_items(self, db, faker_instance, inbox_fixture_root) -> None:
        deps = self._setup(db, faker_instance, inbox_fixture_root)
        orders = orders_gen.generate(faker_instance, *deps)
        orders_gen.persist(db, orders)
        assert db.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 150
        assert db.execute(text("SELECT COUNT(*) FROM order_line_items")).scalar() > 150

    def test_state_transitions_logged(self, db, faker_instance, inbox_fixture_root) -> None:
        deps = self._setup(db, faker_instance, inbox_fixture_root)
        orders = orders_gen.generate(faker_instance, *deps)
        orders_gen.persist(db, orders)
        assert db.execute(text("SELECT COUNT(*) FROM order_state_transitions")).scalar() == 150

    def test_missing_customer_raises_lookup_error(self, db, faker_instance, inbox_fixture_root) -> None:
        # Setup everything except customers
        regions = regions_gen.generate()  # used by sites/apiarists below
        regions_gen.persist(db, regions)
        customers = customers_gen.generate(faker_instance)
        # customers NOT persisted
        sites = sites_gen.generate(faker_instance, customers, regions)
        apiarists = apiarists_gen.generate(faker_instance, regions)
        apiarists_gen.persist(db, apiarists)
        products = products_gen.generate(faker_instance)
        products_gen.persist(db, products)
        services = services_gen.generate()
        services_gen.persist(db, services)
        import seed.emails as emails_mod
        emails_mod.GMAIL_FIXTURE_ROOT = inbox_fixture_root
        emails = emails_gen.generate(faker_instance, customers, apiarists)
        # emails NOT persisted either
        orders = orders_gen.generate(
            faker_instance, customers, sites, apiarists, products, services, emails
        )
        with pytest.raises(LookupError):
            orders_gen.persist(db, orders)
