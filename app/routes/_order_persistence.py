"""Shared helpers for persisting an `OrderDraft` to the database.

Used by both the copilot confirm route (``app.routes.copilot``) and the
inbox confirm route (``app.routes.pages``). Centralizing the persistence
logic keeps the two surfaces consistent — same row shape, same FSM
transition record, same author-attribution rule.

Side effects (per call to ``persist_order_from_draft``):
    * One INSERT into ``orders``
    * One INSERT per line item into ``order_line_items``
    * One INSERT into ``order_state_transitions``
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from agents.schemas import OrderDraft
from db.enums import UserRole
from db.models import Order, OrderLineItem, OrderStateTransition, User

log = logging.getLogger(__name__)


def resolve_default_apiarist(db: Session) -> User:
    """Pick a deterministic active apiarist to author persisted rows.

    No per-user auth in the demo, so confirmed orders / reports need a
    deterministic author. We pick the active apiarist with the lowest
    email alphabetically.

    Raises ``LookupError`` if no apiarist exists — fail-loud per
    ``.claude/rules/fail-loud.md``: the seed pipeline must have run.

    Side effects: one SELECT on ``users``.
    """
    row = db.execute(
        text(
            """
            SELECT id FROM users
            WHERE role = :role AND is_active = TRUE
            ORDER BY email
            LIMIT 1
            """
        ),
        {"role": UserRole.APIARIST.value},
    ).mappings().first()
    if row is None:
        raise LookupError(
            "No active apiarist available to attribute the persisted row. "
            "Did seed.apiarists run? Set up at least one User with "
            "role=apiarist."
        )
    user = db.get(User, _coerce_uuid(row["id"]))
    if user is None:
        raise LookupError(
            "Apiarist row vanished between SELECT and ORM load — "
            "concurrent delete?"
        )
    return user


def persist_order_from_draft(
    draft: OrderDraft,
    db: Session,
    *,
    source_email_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> Order:
    """Write an OrderDraft to ``orders`` + ``order_line_items`` + transition.

    Returns the persisted ``Order`` row. The caller is expected to have
    already verified that ``draft.customer.customer_id`` is set — this
    function raises ``ValueError`` if it isn't, with an actionable
    message.

    Side effects: 1 INSERT on orders, N INSERTs on order_line_items
    (one per draft line item), 1 INSERT on order_state_transitions.
    """
    if draft.customer.customer_id is None:
        raise ValueError(
            "OrderDraft has no resolved customer_id. Cannot persist. "
            "Make sure the customer-match step produced a non-None id "
            "before calling persist_order_from_draft."
        )

    user = resolve_default_apiarist(db)

    order = Order(
        customer_id=draft.customer.customer_id,
        user_id=user.id,
        state=draft.state,
        pollination_date=draft.pollination_date,
        total_usd=draft.total_usd,
        source_email_id=source_email_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    for li in draft.line_items:
        item = OrderLineItem(
            order_id=order.id,
            product_id=li.product_id,
            service_id=li.service_id,
            sku=li.sku,
            quantity=li.quantity,
            unit_price_usd=li.unit_price_usd,
        )
        db.add(item)
    db.commit()

    transition = OrderStateTransition(
        order_id=order.id,
        from_state=None,
        to_state=draft.state,
        actor_id=user.id,
        metadata_=metadata or {},
    )
    db.add(transition)
    db.commit()

    log.info(
        "persisted order %s for customer %s (%d items, $%s)",
        order.id,
        draft.customer.customer_name,
        len(draft.line_items),
        draft.total_usd,
    )
    return order


def _coerce_uuid(val: Any) -> uuid.UUID:
    """Coerce a string-or-UUID DB value to a uuid.UUID."""
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


__all__ = [
    "persist_order_from_draft",
    "resolve_default_apiarist",
]
