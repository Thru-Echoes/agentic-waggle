"""Shared helpers for persisting an `OrderDraft` to the database.

Used by both the copilot confirm route (``app.routes.copilot``) and the
inbox confirm route (``app.routes.pages``). Centralizing the persistence
logic keeps the two surfaces consistent — same row shape, same FSM
transition record, same author-attribution rule.

Also exports ``apply_order_overrides`` — the inline-draft-editing helper
that applies user-edited form fields onto an OrderDraft before persist,
so a salesperson can correct LLM mistakes (quantity, unit price,
pollination date) without re-running extraction.

Side effects (per call to ``persist_order_from_draft``):
    * One INSERT into ``orders``
    * One INSERT per line item into ``order_line_items``
    * One INSERT into ``order_state_transitions``
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import text
from sqlmodel import Session

from agents.schemas import OrderDraft, ResolvedLineItem
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


# ---------------------------------------------------------------------------
# Inline-edit overrides (applied before persist)
# ---------------------------------------------------------------------------


def apply_order_overrides(
    draft: OrderDraft,
    form: Mapping[str, str],
) -> OrderDraft:
    """Return a new OrderDraft with user-edited form fields applied.

    Recognized form keys:
        * ``qty_{i}`` — integer override for ``line_items[i].quantity``
        * ``unit_price_{i}`` — decimal override for
          ``line_items[i].unit_price_usd``
        * ``pollination_date`` — ISO date (YYYY-MM-DD); empty string clears
          the field

    Unrecognized keys are ignored. Out-of-range or unparseable values are
    silently skipped — the original draft value carries through. This is
    deliberate: an empty / unparseable field in the form means "user
    didn't touch this", not "user wants to wipe it." For genuine clear
    semantics on ``pollination_date``, the form must send an empty
    string explicitly.

    Recomputes ``total_usd`` from the (possibly edited) line items so
    persisted Order.total_usd matches what the user actually approved.

    Pure function: returns a new ``OrderDraft``; ``draft`` itself is
    frozen and is not mutated.
    """
    new_items: list[ResolvedLineItem] = []
    for i, li in enumerate(draft.line_items):
        qty = _parse_int(form.get(f"qty_{i}", "")) or li.quantity
        unit = _parse_decimal(form.get(f"unit_price_{i}", ""))
        if unit is None:
            unit = li.unit_price_usd
        # Use model_copy to preserve evidence + confidence + match_method.
        new_items.append(li.model_copy(update={
            "quantity": qty,
            "unit_price_usd": unit,
        }))
    new_total = sum(
        (li.unit_price_usd * li.quantity for li in new_items),
        Decimal("0"),
    )

    new_pollination_date: date | None = draft.pollination_date
    if "pollination_date" in form:
        raw = form["pollination_date"].strip()
        if raw == "":
            new_pollination_date = None
        else:
            try:
                new_pollination_date = date.fromisoformat(raw)
            except ValueError:
                pass  # ignore unparseable; keep original

    return draft.model_copy(update={
        "line_items": new_items,
        "total_usd": new_total,
        "pollination_date": new_pollination_date,
    })


def _parse_int(s: str) -> int | None:
    """Parse a non-negative int form value; return None on failure."""
    s = s.strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n >= 1 else None


def _parse_decimal(s: str) -> Decimal | None:
    """Parse a positive decimal form value; return None on failure."""
    s = s.strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


__all__ = [
    "persist_order_from_draft",
    "resolve_default_apiarist",
    "apply_order_overrides",
]
