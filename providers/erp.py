"""ERP provider — local "Hive-side ERP" mock backed by JSON-line logs.

Honesty guarantees:
  * Writes (`submit_sales_order`, `reconcile_stale`, etc.) append JSON
    lines to the `/erp-sync-log/` directory. Those lines **are** the
    ledger; subsequent reads consult them rather than returning canned
    success.
  * `customer_exists` verifies against the `customers` table using the
    provided SQLAlchemy session. It does NOT return True unconditionally;
    missing customers surface as False and any caller expecting them to
    exist will fail at the next step (which is desirable for catching
    stale assumptions).
  * `fetch_sales_order` replays prior writes from the log; if the SO
    number was never submitted here, it raises LookupError.

Public surface:

    @dataclass
    class ERPResponse:
        erp_so_no: str
        status: str
        ship_via: str | None
        created_at: str
        raw: dict[str, Any]

    class ERPClient:
        def __init__(db: Session | None = None, **kwargs)
        def submit_sales_order(order: dict) -> ERPResponse
        def fetch_sales_order(so_no: str) -> ERPResponse
        def customer_exists(erp_customer_no: str, *, db: Session | None = None) -> bool
        def reconcile_stale(since: datetime) -> list[dict]

Side effects: every call writes a line to `<ERP_LOG_ROOT>/YYYYMMDD.jsonl`.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

ERP_LOG_ROOT = Path("/erp-sync-log")
ERP_LOG_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class ERPResponse:
    erp_so_no: str
    status: str
    ship_via: str | None
    created_at: str
    raw: dict[str, Any]


def _log(event_type: str, payload: dict) -> None:
    """Append a JSON line to today's log file. Side effect: filesystem write."""
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "payload": payload,
    }
    fname = datetime.now(timezone.utc).strftime("%Y%m%d") + ".jsonl"
    with (ERP_LOG_ROOT / fname).open("a") as f:
        f.write(json.dumps(line, default=str) + "\n")


def _iter_log_events() -> Iterator[dict]:
    """Yield every event in the ERP log, oldest file first."""
    for fpath in sorted(ERP_LOG_ROOT.glob("*.jsonl")):
        with fpath.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


class ERPClient:
    """Local Hive-side ERP mock — honest behavior only.

    Accepts an optional DB session at construction for `customer_exists`;
    callers may also pass a session per call.
    """

    def __init__(self, db: Session | None = None, **_: Any) -> None:
        self._db = db

    def submit_sales_order(self, order: dict[str, Any]) -> ERPResponse:
        """Validate and log a sales order. Returns an ERPResponse with a fresh SO number.

        Side effect: appends a `submit_sales_order` event to the log.
        Raises ValueError if the order is missing required fields or has an
        empty line_items list or a non-positive total.
        """
        for required in ("customer_erp_no", "line_items", "total_usd"):
            if required not in order:
                raise ValueError(
                    f"submit_sales_order missing required field {required!r}; "
                    f"got keys {sorted(order.keys())!r}"
                )
        if not order["line_items"]:
            raise ValueError(
                "submit_sales_order: line_items must be non-empty; "
                "an order with zero line items is never valid."
            )
        try:
            from decimal import Decimal
            total = Decimal(str(order["total_usd"]))
        except Exception as exc:  # noqa: BLE001 — re-raised with context
            raise ValueError(
                f"submit_sales_order: total_usd {order['total_usd']!r} is not numeric"
            ) from exc
        if total <= 0:
            raise ValueError(
                f"submit_sales_order: total_usd must be > 0; got {total}"
            )

        so_no = f"SO-LOCAL-{uuid.uuid4().hex[:8].upper()}"
        response = ERPResponse(
            erp_so_no=so_no,
            status="Submitted",
            ship_via=order.get("ship_via"),
            created_at=datetime.now(timezone.utc).isoformat(),
            raw={"ok": True, "so_no": so_no, "hive_side_ref": so_no},
        )
        _log("submit_sales_order", {"input": order, "output": asdict(response)})
        return response

    def fetch_sales_order(self, so_no: str) -> ERPResponse:
        """Return the ERPResponse for a previously-submitted SO.

        Raises LookupError if no matching event is in the log.
        """
        for event in _iter_log_events():
            if event.get("event") != "submit_sales_order":
                continue
            output = event["payload"].get("output", {})
            if output.get("erp_so_no") == so_no:
                return ERPResponse(**output)
        raise LookupError(
            f"No sales order {so_no!r} in ERP log at {ERP_LOG_ROOT}. "
            f"Either submit it first or check the log file."
        )

    def customer_exists(
        self,
        erp_customer_no: str,
        *,
        db: Session | None = None,
    ) -> bool:
        """Return True iff a row exists in `customers` with this ERP number.

        Side effects: runs a SELECT; appends a `customer_exists` log event.
        Raises RuntimeError if no session is available — there is no
        unconditional-True fallback.
        """
        session = db or self._db
        if session is None:
            raise RuntimeError(
                "customer_exists requires a SQLAlchemy Session (either "
                "passed as db= or provided at ERPClient construction). "
                "There is no unconditional-True fallback."
            )
        row = session.execute(
            text("SELECT 1 FROM customers WHERE erp_customer_no = :no LIMIT 1"),
            {"no": erp_customer_no},
        ).first()
        _log(
            "customer_exists",
            {"erp_customer_no": erp_customer_no, "result": row is not None},
        )
        return row is not None

    def reconcile_stale(self, since: datetime) -> list[dict[str, Any]]:
        """Return every sales order submitted on or after `since`.

        Side effect: appends a `reconcile_stale` log event.
        """
        _log("reconcile_stale", {"since": since.isoformat()})
        out: list[dict[str, Any]] = []
        for event in _iter_log_events():
            if event.get("event") != "submit_sales_order":
                continue
            ts = datetime.fromisoformat(event["ts"])
            if ts >= since:
                out.append(event["payload"].get("output", {}))
        return out
