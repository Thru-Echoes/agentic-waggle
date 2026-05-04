"""Pydantic domain model validation — no DB needed."""
# pyright: reportMissingImports=false
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from seed import models


class TestRegion:
    def test_valid(self) -> None:
        r = models.Region(
            code="test_region",
            name="Test",
            primary_crops=["almond"],
            peak_start_month=2,
            peak_end_month=3,
        )
        assert r.peak_start_month == 2

    @pytest.mark.parametrize("month", [0, 13, -1])
    def test_peak_month_out_of_range_fails_loudly(self, month: int) -> None:
        with pytest.raises(ValidationError):
            models.Region(
                code="x",
                name="X",
                primary_crops=[],
                peak_start_month=month,
                peak_end_month=6,
            )


class TestApiarist:
    def test_valid(self) -> None:
        a = models.Apiarist(
            email="a@b.test",
            name="A",
            role="apiarist",
            erp_apiarist_no="APR-0001",
            region_codes=["central_valley"],
        )
        assert a.role == "apiarist"

    def test_invalid_role_fails(self) -> None:
        with pytest.raises(ValidationError):
            models.Apiarist(
                email="a@b.test",
                name="A",
                role="superuser",  # type: ignore[arg-type]
                erp_apiarist_no="APR-0001",
                region_codes=[],
            )


class TestCustomer:
    def test_valid(self) -> None:
        c = models.Customer(
            name="Test",
            erp_customer_no="C-00001",
            market_segment="ORCHARD_ALMOND",
        )
        assert c.aliases == []

    def test_invalid_segment_fails(self) -> None:
        with pytest.raises(ValidationError):
            models.Customer(
                name="Test",
                erp_customer_no="C-00001",
                market_segment="CONSUMER",  # type: ignore[arg-type]
            )


class TestOrder:
    def test_valid_with_line_items(self) -> None:
        o = models.Order(
            customer_name="Test",
            apiarist_email="a@b.test",
            state="APPROVED",
            pollination_date=date(2026, 3, 1),
            total_usd=Decimal("1000.00"),
            line_items=[
                models.OrderLineItem(
                    sku="POL_ALMOND_PREMIUM",
                    quantity=100,
                    unit_price_usd=Decimal("220.00"),
                )
            ],
        )
        assert o.state == "APPROVED"

    def test_invalid_state_fails(self) -> None:
        with pytest.raises(ValidationError):
            models.Order(
                customer_name="x",
                apiarist_email="x@x.test",
                state="PENDING_REVIEW",  # type: ignore[arg-type]
                total_usd=Decimal("100"),
                line_items=[],
            )


class TestEmail:
    def test_valid(self) -> None:
        e = models.Email(
            external_id="abc123",
            thread_id="abc123",
            account_id="a@b.test",
            from_address="x@y.test",
            to_addresses=["a@b.test"],
            subject="Hi",
            body_text="body",
            received_at=datetime.now(timezone.utc),
        )
        assert e.external_id == "abc123"


class TestInspectionReport:
    def test_valid(self) -> None:
        r = models.InspectionReport(
            customer_name="Test",
            site_name="Block 1",
            author_email="a@b.test",
            visit_date=date(2026, 2, 14),
            hives_placed=400,
            varroa_count=Decimal("1.20"),
            diseases_observed=["NONE"],
            narrative="x",
        )
        assert r.hives_placed == 400

    def test_invalid_disease_code_fails(self) -> None:
        with pytest.raises(ValidationError):
            models.InspectionReport(
                customer_name="x",
                site_name="x",
                author_email="x@y.test",
                visit_date=date(2026, 1, 1),
                hives_placed=100,
                varroa_count=Decimal("1"),
                diseases_observed=["COVID"],  # type: ignore[list-item]
                narrative="x",
            )


class TestRegulatedItem:
    def test_invalid_restriction_type_fails(self) -> None:
        with pytest.raises(ValidationError):
            models.RegulatedItem(
                product_sku="x",
                restriction_type="made_up",  # type: ignore[arg-type]
                restricted_states=[],
            )


class TestRedFlagRule:
    def test_invalid_scope_fails(self) -> None:
        with pytest.raises(ValidationError):
            models.RedFlagRule(
                rule_name="x",
                scope="account",  # type: ignore[arg-type]
                description="x",
                severity="warn",
            )

    def test_invalid_severity_fails(self) -> None:
        with pytest.raises(ValidationError):
            models.RedFlagRule(
                rule_name="x",
                scope="order",
                description="x",
                severity="catastrophic",  # type: ignore[arg-type]
            )
