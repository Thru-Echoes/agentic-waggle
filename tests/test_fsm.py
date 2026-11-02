"""Order FSM contract: the table matches the published diagram, every write
goes through ``transition_order``, and illegal or stale moves raise.

The enumeration guard at the bottom fails when a new module inserts into
``order_state_transitions`` or updates ``orders.state`` by hand, so the
invariant cannot silently regress.
"""
from __future__ import annotations

import ast
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from db import fsm
from db.enums import OrderState
from db.models import Order

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Table shape (no DB)
# ---------------------------------------------------------------------------


def test_every_state_has_an_entry() -> None:
    assert set(fsm.ALLOWED_TRANSITIONS) == set(OrderState)


def test_terminal_states_are_exactly_synced_and_cancelled() -> None:
    assert fsm.TERMINAL_STATES == {OrderState.SYNCED_TO_ERP, OrderState.CANCELLED}
    for s in fsm.TERMINAL_STATES:
        assert fsm.get_allowed_next_states(s) == ()


def test_published_diagram_edges_are_allowed() -> None:
    """Edges from the architecture page's state diagram."""
    diagram = [
        ("DRAFT", "EXTRACTED"),
        ("EXTRACTED", "UNDER_REVIEW"),
        ("UNDER_REVIEW", "APPROVED"),
        ("UNDER_REVIEW", "EXTRACTED"),
        ("UNDER_REVIEW", "CANCELLED"),
        ("APPROVED", "SCHEDULED"),
        ("APPROVED", "PACKED"),
        ("SCHEDULED", "IN_PROGRESS"),
        ("IN_PROGRESS", "FULFILLED"),
        ("PACKED", "FULFILLED"),
        ("FULFILLED", "INVOICED"),
        ("INVOICED", "SYNCED_TO_ERP"),
    ]
    for a, b in diagram:
        assert OrderState(b) in fsm.ALLOWED_TRANSITIONS[OrderState(a)], (a, b)


def test_no_backwards_or_skipping_edges_after_approval() -> None:
    late = [OrderState.SCHEDULED, OrderState.PACKED, OrderState.IN_PROGRESS,
            OrderState.FULFILLED, OrderState.INVOICED]
    for s in late:
        nxt = fsm.ALLOWED_TRANSITIONS[s]
        assert OrderState.EXTRACTED not in nxt and OrderState.UNDER_REVIEW not in nxt
        assert OrderState.SYNCED_TO_ERP not in nxt or s is OrderState.INVOICED


def test_initial_states_are_draft_and_extracted() -> None:
    assert fsm.INITIAL_STATES == {OrderState.DRAFT, OrderState.EXTRACTED}
    fsm.validate_initial_state(OrderState.DRAFT)
    fsm.validate_initial_state(OrderState.EXTRACTED)
    with pytest.raises(fsm.IllegalTransition, match="cannot start in state APPROVED"):
        fsm.validate_initial_state(OrderState.APPROVED)


def test_transition_labels_cover_every_non_cancel_edge() -> None:
    for a, nxt in fsm.ALLOWED_TRANSITIONS.items():
        for b in nxt:
            if b is OrderState.CANCELLED:
                assert fsm.get_transition_label(a, b) == "cancel"
            else:
                assert (a, b) in fsm.TRANSITION_LABELS, (a, b)


# ---------------------------------------------------------------------------
# Runtime behaviour (DB)
# ---------------------------------------------------------------------------


def _make_order(db: Session, faker_instance, state: OrderState = OrderState.EXTRACTED) -> Order:
    from seed import apiarists as apiarists_gen
    from seed import customers as customers_gen
    from seed import regions as regions_gen

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers_gen.persist(db, customers_gen.generate(faker_instance))
    apiarists_gen.persist(db, apiarists_gen.generate(faker_instance, regions))
    customer_id = db.execute(text("SELECT id FROM customers LIMIT 1")).scalar_one()
    user_id = db.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
    order = Order(
        customer_id=uuid.UUID(str(customer_id)),
        user_id=uuid.UUID(str(user_id)),
        state=state,
        total_usd=Decimal("100.00"),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_legal_transition_updates_order_and_records_row(db: Session, faker_instance) -> None:
    order = _make_order(db, faker_instance)
    row = fsm.transition_order(
        db, order, OrderState.UNDER_REVIEW, actor_id=order.user_id,
        metadata={"source": "test"}, expected_from_state=OrderState.EXTRACTED,
    )
    db.commit()
    assert order.state is OrderState.UNDER_REVIEW
    assert row.from_state is OrderState.EXTRACTED and row.to_state is OrderState.UNDER_REVIEW
    stored = db.execute(
        text("SELECT from_state, to_state, metadata->>'source' FROM order_state_transitions WHERE order_id = :id"),
        {"id": str(order.id)},
    ).all()
    assert stored == [("EXTRACTED", "UNDER_REVIEW", "test")]
    assert db.execute(text("SELECT state FROM orders WHERE id = :id"), {"id": str(order.id)}).scalar_one() == "UNDER_REVIEW"


def test_illegal_transition_raises_and_writes_nothing(db: Session, faker_instance) -> None:
    order = _make_order(db, faker_instance)
    with pytest.raises(fsm.IllegalTransition, match="cannot move from EXTRACTED to SYNCED_TO_ERP"):
        fsm.transition_order(db, order, OrderState.SYNCED_TO_ERP, actor_id=None)
    db.rollback()
    assert db.execute(text("SELECT COUNT(*) FROM order_state_transitions")).scalar_one() == 0
    assert db.execute(text("SELECT state FROM orders WHERE id = :id"), {"id": str(order.id)}).scalar_one() == "EXTRACTED"


def test_same_state_is_not_a_transition(db: Session, faker_instance) -> None:
    order = _make_order(db, faker_instance)
    with pytest.raises(fsm.IllegalTransition):
        fsm.transition_order(db, order, OrderState.EXTRACTED, actor_id=None)


def test_stale_expectation_raises(db: Session, faker_instance) -> None:
    order = _make_order(db, faker_instance)
    fsm.transition_order(db, order, OrderState.UNDER_REVIEW, actor_id=None)
    with pytest.raises(fsm.StaleTransition, match="is in state UNDER_REVIEW, not EXTRACTED"):
        fsm.transition_order(
            db, order, OrderState.UNDER_REVIEW, actor_id=None,
            expected_from_state=OrderState.EXTRACTED,
        )


def test_full_happy_path_to_erp_is_legal(db: Session, faker_instance) -> None:
    order = _make_order(db, faker_instance)
    path = [OrderState.UNDER_REVIEW, OrderState.APPROVED, OrderState.SCHEDULED,
            OrderState.IN_PROGRESS, OrderState.FULFILLED, OrderState.INVOICED,
            OrderState.SYNCED_TO_ERP]
    for s in path:
        fsm.transition_order(db, order, s, actor_id=None)
    db.commit()
    assert order.state is OrderState.SYNCED_TO_ERP
    n = db.execute(text("SELECT COUNT(*) FROM order_state_transitions WHERE order_id = :id"), {"id": str(order.id)}).scalar_one()
    assert n == len(path)
    with pytest.raises(fsm.IllegalTransition, match="terminal"):
        fsm.transition_order(db, order, OrderState.CANCELLED, actor_id=None)


# ---------------------------------------------------------------------------
# Enumeration guard: nobody writes transitions or order states by hand
# ---------------------------------------------------------------------------

_SCAN_DIRS = ("app", "agents", "providers", "mcp_servers", "scripts", "db")
_RAW_TRANSITION_INSERT = re.compile(r"INSERT\s+INTO\s+order_state_transitions", re.IGNORECASE)
_RAW_STATE_UPDATE = re.compile(r"UPDATE\s+orders\s+SET\s+state", re.IGNORECASE)


def _python_files() -> list[Path]:
    out: list[Path] = []
    for d in _SCAN_DIRS:
        out.extend(p for p in (REPO_ROOT / d).rglob("*.py") if ".venv" not in p.parts)
    return out


def test_no_module_writes_transitions_or_order_state_by_hand() -> None:
    """Raw SQL guard: the strings a hand-written path would contain."""
    offenders: list[str] = []
    for path in _python_files():
        src = path.read_text()
        if _RAW_TRANSITION_INSERT.search(src) or _RAW_STATE_UPDATE.search(src):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"use db.fsm.transition_order instead of raw SQL in: {offenders}"


def test_no_module_constructs_transition_rows_outside_fsm() -> None:
    """ORM guard: ``OrderStateTransition(...)`` is called only in db/fsm.py."""
    offenders: list[str] = []
    for path in _python_files():
        if path.name == "fsm.py" and path.parent.name == "db":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
                if name == "OrderStateTransition":
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], f"construct transitions via db.fsm.transition_order: {offenders}"
