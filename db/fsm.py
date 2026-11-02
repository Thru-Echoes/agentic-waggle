"""Order state machine: the one place that knows which transitions are legal.

``ALLOWED_TRANSITIONS`` mirrors the published state diagram. Every write to
``orders.state`` and every row in ``order_state_transitions`` goes through
``transition_order`` (or, for a brand-new order, ``validate_initial_state``),
so an illegal move raises instead of being recorded. ``tests/test_fsm.py``
enumerates the table and checks that no module inserts transition rows by
hand.

Public surface:

    ALLOWED_TRANSITIONS: Mapping[OrderState, frozenset[OrderState]]
    INITIAL_STATES: frozenset[OrderState]
    TERMINAL_STATES: frozenset[OrderState]
    class IllegalTransition(ValueError)
    class StaleTransition(IllegalTransition)
    get_allowed_next_states(state) -> tuple[OrderState, ...]
    validate_initial_state(state) -> None
    record_creation(db, order, *, actor_id, metadata) -> OrderStateTransition
    transition_order(db, order, to_state, *, actor_id, metadata, expected_from_state) -> OrderStateTransition

Side effects: ``transition_order`` mutates the ``Order`` row, adds one
``OrderStateTransition`` and flushes; the caller owns the commit.
"""
from __future__ import annotations

import uuid
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy.orm import Session

from db.enums import OrderState
from db.models import Order, OrderStateTransition, utc_now

ALLOWED_TRANSITIONS: Mapping[OrderState, frozenset[OrderState]] = MappingProxyType({
    OrderState.DRAFT: frozenset({OrderState.EXTRACTED, OrderState.CANCELLED}),
    OrderState.EXTRACTED: frozenset({OrderState.UNDER_REVIEW, OrderState.CANCELLED}),
    OrderState.UNDER_REVIEW: frozenset({
        OrderState.APPROVED, OrderState.EXTRACTED, OrderState.CANCELLED,
    }),
    OrderState.APPROVED: frozenset({
        OrderState.SCHEDULED, OrderState.PACKED, OrderState.CANCELLED,
    }),
    OrderState.SCHEDULED: frozenset({OrderState.IN_PROGRESS, OrderState.CANCELLED}),
    OrderState.PACKED: frozenset({OrderState.FULFILLED, OrderState.CANCELLED}),
    OrderState.IN_PROGRESS: frozenset({OrderState.FULFILLED}),
    OrderState.FULFILLED: frozenset({OrderState.INVOICED}),
    OrderState.INVOICED: frozenset({OrderState.SYNCED_TO_ERP}),
    OrderState.SYNCED_TO_ERP: frozenset(),
    OrderState.CANCELLED: frozenset(),
})

#: States a brand-new order may be created in: a rep's draft, or the agent's
#: extraction. Everything else must be reached through a transition.
INITIAL_STATES: frozenset[OrderState] = frozenset({OrderState.DRAFT, OrderState.EXTRACTED})

TERMINAL_STATES: frozenset[OrderState] = frozenset(
    s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt
)

#: Human-readable meaning of each edge, shown next to the Advance buttons.
TRANSITION_LABELS: Mapping[tuple[OrderState, OrderState], str] = MappingProxyType({
    (OrderState.DRAFT, OrderState.EXTRACTED): "run extraction",
    (OrderState.EXTRACTED, OrderState.UNDER_REVIEW): "queue for review",
    (OrderState.UNDER_REVIEW, OrderState.APPROVED): "approve",
    (OrderState.UNDER_REVIEW, OrderState.EXTRACTED): "return with notes",
    (OrderState.APPROVED, OrderState.SCHEDULED): "schedule pollination",
    (OrderState.APPROVED, OrderState.PACKED): "pack product order",
    (OrderState.SCHEDULED, OrderState.IN_PROGRESS): "hives deployed",
    (OrderState.IN_PROGRESS, OrderState.FULFILLED): "contract complete",
    (OrderState.PACKED, OrderState.FULFILLED): "shipped",
    (OrderState.FULFILLED, OrderState.INVOICED): "invoice",
    (OrderState.INVOICED, OrderState.SYNCED_TO_ERP): "sync to ERP",
})


class IllegalTransition(ValueError):
    """The requested move is not an edge of the state machine."""


class StaleTransition(IllegalTransition):
    """The order is no longer in the state the caller last saw."""


def get_allowed_next_states(state: OrderState) -> tuple[OrderState, ...]:
    """Return the legal next states for ``state`` in diagram order.

    Side effects: none.
    """
    return tuple(s for s in OrderState if s in ALLOWED_TRANSITIONS[state])


def get_transition_label(from_state: OrderState, to_state: OrderState) -> str:
    """Return the short verb for an edge; cancellation is uniform."""
    if to_state is OrderState.CANCELLED:
        return "cancel"
    return TRANSITION_LABELS.get((from_state, to_state), to_state.value.lower().replace("_", " "))


def validate_initial_state(state: OrderState) -> None:
    """Raise ``IllegalTransition`` unless a new order may start in ``state``.

    Side effects: none.
    """
    if state not in INITIAL_STATES:
        raise IllegalTransition(
            f"A new order cannot start in state {state.value}. "
            f"New orders start in one of {sorted(s.value for s in INITIAL_STATES)}; "
            "later states are reached through transition_order()."
        )


def transition_order(
    db: Session,
    order: Order,
    to_state: OrderState,
    *,
    actor_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
    expected_from_state: OrderState | None = None,
) -> OrderStateTransition:
    """Move ``order`` to ``to_state`` and record the transition.

    Parameters:
        db: Session the order belongs to; the transition row is added to it.
        order: The ORM row to move. Its ``state`` and ``updated_at`` change.
        to_state: Target state; must be in ``ALLOWED_TRANSITIONS[order.state]``.
        actor_id: User performing the move (``None`` for a system step).
        metadata: Free-form JSON stored on the transition row.
        expected_from_state: When given, the state the caller believes the
            order is in. A mismatch raises ``StaleTransition`` so a UI that
            rendered the order a moment ago cannot apply an outdated move.

    Returns:
        The ``OrderStateTransition`` row that was added (flushed, not committed).

    Raises:
        StaleTransition: ``expected_from_state`` does not match ``order.state``.
        IllegalTransition: the edge is not in the table (including a move to
            the current state).

    Side effects: mutates ``order``, adds one row, flushes the session.
    """
    current = OrderState(order.state)
    if expected_from_state is not None and expected_from_state is not current:
        raise StaleTransition(
            f"Order {order.id} is in state {current.value}, not "
            f"{expected_from_state.value}; reload and choose again."
        )
    allowed = ALLOWED_TRANSITIONS[current]
    if to_state not in allowed:
        options = ", ".join(s.value for s in get_allowed_next_states(current)) or "none (terminal)"
        raise IllegalTransition(
            f"Order {order.id} cannot move from {current.value} to {to_state.value}. "
            f"Allowed next states: {options}."
        )
    transition = OrderStateTransition(
        order_id=order.id,
        from_state=current,
        to_state=to_state,
        actor_id=actor_id,
        metadata_=dict(metadata or {}),
    )
    order.state = to_state
    order.updated_at = utc_now()
    db.add(order)
    db.add(transition)
    db.flush()
    return transition


def record_creation(
    db: Session,
    order: Order,
    *,
    actor_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
) -> OrderStateTransition:
    """Record that ``order`` came into existence in its current state.

    The creation row has ``from_state`` NULL. The state must be one of
    ``INITIAL_STATES``; this is the only way a transition row without a
    source state is written.

    Side effects: adds one row and flushes; the caller commits.
    """
    state = OrderState(order.state)
    validate_initial_state(state)
    transition = OrderStateTransition(
        order_id=order.id,
        from_state=None,
        to_state=state,
        actor_id=actor_id,
        metadata_=dict(metadata or {}),
    )
    db.add(transition)
    db.flush()
    return transition


__all__ = [
    "ALLOWED_TRANSITIONS",
    "INITIAL_STATES",
    "TERMINAL_STATES",
    "TRANSITION_LABELS",
    "IllegalTransition",
    "StaleTransition",
    "get_allowed_next_states",
    "get_transition_label",
    "record_creation",
    "transition_order",
    "validate_initial_state",
]
