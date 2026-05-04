"""10 red-flag rules. Scope-aware, aligned to `plans/05-beekeeping-detailed-plan.md`."""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import RedFlagRule

RULES: list[RedFlagRule] = [
    RedFlagRule(
        rule_name="site_afb_history_24mo",
        scope="site",
        description="American foulbrood confirmed at this site in the last 24 months",
        severity="block",
    ),
    RedFlagRule(
        rule_name="customer_varroa_trend_rising",
        scope="customer",
        description="Varroa mite count trending above threshold across recent inspections",
        severity="warn",
    ),
    RedFlagRule(
        rule_name="site_pesticide_spray_during_bloom",
        scope="site",
        description="Pesticide application scheduled during declared bloom window",
        severity="block",
    ),
    RedFlagRule(
        rule_name="user_apiary_registration_expired",
        scope="user",
        description="Apiarist's home-state apiary registration is expired or lapsing",
        severity="warn",
    ),
    RedFlagRule(
        rule_name="order_queen_to_restricted_state",
        scope="order",
        description="Queen shipment destination is a quarantine state without certificate",
        severity="block",
    ),
    RedFlagRule(
        rule_name="order_hive_count_over_500",
        scope="order",
        description="Pollination contract exceeds 500 hives (escalation)",
        severity="warn",
    ),
    RedFlagRule(
        rule_name="customer_overdue_60d",
        scope="customer",
        description="Customer invoice overdue by more than 60 days",
        severity="block",
    ),
    RedFlagRule(
        rule_name="customer_credit_limit_exceeded",
        scope="customer",
        description="Order would put customer over annual credit limit",
        severity="warn",
    ),
    RedFlagRule(
        rule_name="customer_organic_cert_expired",
        scope="customer",
        description="Organic certification expired; organic contract at risk",
        severity="info",
    ),
    RedFlagRule(
        rule_name="order_vsh_lineage_unverifiable",
        scope="order",
        description="VSH queen lineage cannot be verified against supplier records",
        severity="warn",
    ),
]


def generate() -> list[RedFlagRule]:
    return RULES


def persist(db: Session, rules: list[RedFlagRule]) -> None:
    """Duplicate rule_name raises IntegrityError."""
    for r in rules:
        db.execute(
            text(
                "INSERT INTO red_flag_rules "
                "(id, rule_name, scope, description, severity, is_active) "
                "VALUES (:id, :name, :scope, :desc, :sev, true)"
            ),
            {
                "id": str(uuid.uuid4()),
                "name": r.rule_name,
                "scope": r.scope,
                "desc": r.description,
                "sev": r.severity,
            },
        )
