"""E2E tests for the ERP provider."""
# pyright: reportMissingImports=false
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from providers.erp import ERPClient


VALID_ORDER: dict = {
    "customer_erp_no": "C-01001",
    "line_items": [{"sku": "POL_ALMOND_PREMIUM", "quantity": 400}],
    "total_usd": "88000.00",
    "ship_via": "Flatbed",
}


class TestSubmitSalesOrder:
    def test_returns_so_number_and_logs(self, erp_log_root) -> None:
        c = ERPClient()
        resp = c.submit_sales_order(VALID_ORDER)
        assert resp.erp_so_no.startswith("SO-LOCAL-")
        assert resp.status == "Submitted"

        logs = list(erp_log_root.glob("*.jsonl"))
        assert len(logs) == 1
        lines = logs[0].read_text().strip().split("\n")
        assert len(lines) == 1

    def test_missing_customer_erp_no_raises(self, erp_log_root) -> None:
        c = ERPClient()
        bad = dict(VALID_ORDER)
        del bad["customer_erp_no"]
        with pytest.raises(ValueError):
            c.submit_sales_order(bad)

    def test_missing_line_items_raises(self, erp_log_root) -> None:
        c = ERPClient()
        bad = dict(VALID_ORDER)
        del bad["line_items"]
        with pytest.raises(ValueError):
            c.submit_sales_order(bad)

    def test_empty_line_items_raises(self, erp_log_root) -> None:
        c = ERPClient()
        bad = dict(VALID_ORDER)
        bad["line_items"] = []
        with pytest.raises(ValueError, match="line_items must be non-empty"):
            c.submit_sales_order(bad)

    def test_zero_total_raises(self, erp_log_root) -> None:
        c = ERPClient()
        bad = dict(VALID_ORDER)
        bad["total_usd"] = "0.00"
        with pytest.raises(ValueError, match="total_usd must be > 0"):
            c.submit_sales_order(bad)

    def test_negative_total_raises(self, erp_log_root) -> None:
        c = ERPClient()
        bad = dict(VALID_ORDER)
        bad["total_usd"] = "-100.00"
        with pytest.raises(ValueError, match="total_usd must be > 0"):
            c.submit_sales_order(bad)

    def test_non_numeric_total_raises(self, erp_log_root) -> None:
        c = ERPClient()
        bad = dict(VALID_ORDER)
        bad["total_usd"] = "not-a-number"
        with pytest.raises(ValueError, match="is not numeric"):
            c.submit_sales_order(bad)


class TestFetchSalesOrder:
    def test_fetch_returns_previously_submitted(self, erp_log_root) -> None:
        c = ERPClient()
        resp = c.submit_sales_order(VALID_ORDER)
        fetched = c.fetch_sales_order(resp.erp_so_no)
        assert fetched.erp_so_no == resp.erp_so_no

    def test_fetch_unknown_so_raises(self, erp_log_root) -> None:
        c = ERPClient()
        with pytest.raises(LookupError):
            c.fetch_sales_order("SO-LOCAL-NONEXISTENT")


class TestCustomerExists:
    def test_requires_session(self, erp_log_root) -> None:
        c = ERPClient()
        with pytest.raises(RuntimeError):
            c.customer_exists("C-01001")

    def test_returns_true_for_seeded_customer(self, db, erp_log_root, faker_instance) -> None:
        from seed import customers as customers_gen
        customers = customers_gen.generate(faker_instance)
        customers_gen.persist(db, customers)
        known_no = customers[0].erp_customer_no

        c = ERPClient(db=db)
        assert c.customer_exists(known_no) is True

    def test_returns_false_for_missing_customer(self, db, erp_log_root) -> None:
        c = ERPClient(db=db)
        assert c.customer_exists("C-DOES-NOT-EXIST") is False


class TestReconcileStale:
    def test_returns_orders_submitted_after_cutoff(self, erp_log_root) -> None:
        c = ERPClient()
        c.submit_sales_order(VALID_ORDER)
        cutoff = datetime(2020, 1, 1, tzinfo=timezone.utc)
        out = c.reconcile_stale(cutoff)
        assert len(out) == 1

    def test_empty_before_any_submissions(self, erp_log_root) -> None:
        c = ERPClient()
        out = c.reconcile_stale(datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert out == []
