"""10 approval rules governing order state transitions into APPROVED."""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import ApprovalRule

RULES: list[ApprovalRule] = [
    ApprovalRule(
        rule_name="pollination_over_500_hives",
        condition_description="Pollination contract exceeding 500 hives",
        requires_role="ops_manager",
    ),
    ApprovalRule(
        rule_name="pollination_with_active_red_flag",
        condition_description="Any order with an unresolved blocking red flag",
        requires_role="ops_manager",
    ),
    ApprovalRule(
        rule_name="pollination_into_restricted_state",
        condition_description="Pollination contract or queen delivery to HI/CA quarantine zones",
        requires_role="admin",
    ),
    ApprovalRule(
        rule_name="product_order_over_50k",
        condition_description="Any product order exceeding $50,000",
        requires_role="ops_manager",
    ),
    ApprovalRule(
        rule_name="queen_import_restricted_state",
        condition_description="Queen order crossing state line into CA/FL/HI",
        requires_role="admin",
    ),
    ApprovalRule(
        rule_name="miticide_unlicensed_buyer",
        condition_description="EPA-registered miticide sold to buyer without license on file",
        requires_role="reject",
    ),
    ApprovalRule(
        rule_name="new_customer_first_order",
        condition_description="First order from a newly onboarded customer",
        requires_role="ops_manager",
    ),
    ApprovalRule(
        rule_name="customer_overdue_balance",
        condition_description="Customer has an overdue balance; block until cleared",
        requires_role="reject",
    ),
    ApprovalRule(
        rule_name="sandbox_mode_auto_approve",
        condition_description="Sandbox mode active — auto-approve without ERP push",
        requires_role="none",
    ),
    ApprovalRule(
        rule_name="default_auto_approve",
        condition_description="No other rule matched — auto-approve",
        requires_role="none",
    ),
]


def generate() -> list[ApprovalRule]:
    return RULES


def persist(db: Session, rules: list[ApprovalRule]) -> None:
    """Duplicate rule_name raises IntegrityError."""
    for r in rules:
        db.execute(
            text(
                "INSERT INTO approval_rules "
                "(id, rule_name, condition_description, requires_role, is_active) "
                "VALUES (:id, :name, :desc, :role, true)"
            ),
            {
                "id": str(uuid.uuid4()),
                "name": r.rule_name,
                "desc": r.condition_description,
                "role": r.requires_role,
            },
        )
