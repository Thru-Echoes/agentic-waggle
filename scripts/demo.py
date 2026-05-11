"""Waggle — end-to-end demo orchestrator. Walks every surface of the stack.

Usage (from inside the seed/api container, or the local conda env with
DATABASE_URL pointing at the Waggle DB):

    python -m scripts.demo
    python -m scripts.demo --account-id rep@example.test
    python -m scripts.demo --account-id rep@example.test --message-id m12

Walks through (each step is labeled in the output):

    [1/7] Preflight       — DB reachable, pgvector present, seed data loaded.
    [2/7] Inbox pull      — fetch one unread inbound email via providers.inbox.
    [3/7] Agent           — run the LangGraph extraction pipeline on the email.
    [4/7] FSM + ERP       — persist the OrderDraft, drive it DRAFT→…→SYNCED_TO_ERP,
                            log the ERP call via providers.erp.
    [5/7] RAG follow-up   — search customer reports + draft a follow-up email
                            (legacy copilot path — still useful for portfolio).
    [6/7] Chat copilot    — open a conversation, post a turn through the
                            orchestrator, persist user + assistant ChatTurns,
                            print the assistant reply.
    [7/7] MCP tool call   — invoke a sales-copilot MCP tool via fastmcp's
                            in-process Client so the demo reaches the
                            Model Context Protocol surface too.

Anything missing (no inbox fixture, no LLM API key, unseeded DB) is a
hard failure with an actionable message. There is no silent-skip mode.

Side effects:
    * DB writes: one `orders` row, one `order_line_items` row per resolved
      line item, one `order_state_transitions` per FSM step, one
      `conversations` row plus two `chat_turns` rows for the chat copilot step.
    * Filesystem: appends to `/erp-sync-log/YYYYMMDD.jsonl` and writes one
      JSON file to `/sent-mail/`.
    * LLM: 3-5 Anthropic/OpenAI calls for the agent + 1 for the RAG follow-up
      + 1-3 for the chat copilot turn + (potentially) 1 for the MCP tool.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.order_extractor import run_order_extraction
from agents.schemas import EmailInput, ExtractionFailure, OrderDraft
from db.enums import OrderState
from db.session import session_scope
from mcp_servers.sales_copilot_server import (
    _format_email_context,
    _parse_subject_body,
)
from providers.erp import ERPClient
from providers.inbox import InboxClient
from providers.llm import LLMClient
from providers.mailer import send as send_mail
from providers.vector_search import VectorSearchProvider

log = logging.getLogger("demo")

DEFAULT_ACCOUNT_ID = "isa.martinez@meridian-apiaries.test"


# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------


def _banner(label: str) -> None:
    print("\n" + "━" * 72)
    print(f"  {label}")
    print("━" * 72)


def _step(num: str, title: str) -> None:
    print(f"\n[{num}] {title}")
    print("─" * 72)


def _indent(s: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in s.splitlines())


# ---------------------------------------------------------------------------
# Step 1 — preflight
# ---------------------------------------------------------------------------


def preflight(db: Session) -> None:
    """Fail loud if the stack isn't ready for a demo."""
    db.execute(text("SELECT 1")).scalar_one()

    pgv = db.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).first()
    if pgv is None:
        raise RuntimeError(
            "pgvector not installed. Run `make migrate` — the first migration "
            "creates the extension."
        )

    counts = {
        "customers": db.execute(
            text("SELECT COUNT(*) FROM customers")
        ).scalar_one(),
        "orders": db.execute(
            text("SELECT COUNT(*) FROM orders")
        ).scalar_one(),
        "customer_reports": db.execute(
            text("SELECT COUNT(*) FROM customer_reports")
        ).scalar_one(),
        "chunks": db.execute(
            text("SELECT COUNT(*) FROM chunks")
        ).scalar_one(),
        "synced_emails": db.execute(
            text("SELECT COUNT(*) FROM synced_emails")
        ).scalar_one(),
    }
    for table, n in counts.items():
        if n == 0:
            raise RuntimeError(
                f"Table `{table}` is empty. Run `make seed` (or `make reseed`) "
                f"before the demo."
            )
    print("    stack reachable:")
    for table, n in counts.items():
        print(f"      {table:15s}  {n:>5d}")

    # LLM: we don't call yet, but surface the misconfig before step 3.
    try:
        LLMClient()
    except RuntimeError as exc:
        raise RuntimeError(
            "LLM client cannot be constructed — the demo requires a real "
            "model. Set ANTHROPIC_API_KEY (preferred) or OPENAI_API_KEY."
        ) from exc
    print("    LLM api key set ✓")


# ---------------------------------------------------------------------------
# Step 2 — inbox pull
# ---------------------------------------------------------------------------


def pull_email(
    account_id: str,
    message_id: str | None,
) -> EmailInput:
    """Grab one unread email (or a specific message) from the inbox provider."""
    inbox = InboxClient()
    if message_id is not None:
        msg = inbox.fetch(account_id, message_id)
    else:
        unread = inbox.list_unread(account_id)
        if not unread:
            raise RuntimeError(
                f"No unread mail for {account_id!r}. Pass --message-id to "
                f"replay a specific message, or run `make reseed`."
            )
        msg = unread[0]

    print(f"    from:    {msg.from_address}")
    print(f"    subject: {msg.subject}")
    print(f"    id:      {msg.id}")
    print()
    print(_indent(msg.body_text[:600] + ("…" if len(msg.body_text) > 600 else "")))

    return EmailInput(
        external_id=msg.id,
        from_address=msg.from_address,
        subject=msg.subject,
        body_text=msg.body_text,
    )


# ---------------------------------------------------------------------------
# Step 3 — agent
# ---------------------------------------------------------------------------


def run_agent(db: Session, email: EmailInput) -> OrderDraft:
    """Run the LangGraph pipeline, fail loudly on ExtractionFailure."""
    result = run_order_extraction(email, db)
    if isinstance(result, ExtractionFailure):
        raise RuntimeError(
            f"Agent failed at stage {result.stage!r}: {result.reason}"
        )
    assert isinstance(result, OrderDraft)

    customer = result.customer
    print(f"    customer:  {customer.customer_name!r} "
          f"(score={customer.score:.1f}"
          f"{', ambiguous' if customer.ambiguous else ''})")
    print(f"    state:     {result.state.value}")
    print(f"    total:     ${result.total_usd}")
    print(f"    items:     {len(result.line_items)}")
    for li in result.line_items:
        print(
            f"      - {li.sku:30s}  qty={li.quantity:<5d}  "
            f"@${li.unit_price_usd:<10}  via {li.match_method}"
        )
    if result.extraction_concerns:
        print("    concerns:")
        for n in result.extraction_concerns:
            print(f"      • {n}")
    return result


# ---------------------------------------------------------------------------
# Step 4 — persist + FSM + ERP
# ---------------------------------------------------------------------------


def persist_and_advance(db: Session, draft: OrderDraft) -> uuid.UUID:
    """Write the draft to `orders` + `order_line_items`, record FSM history,
    call the ERP mock, then flip state to SYNCED_TO_ERP.

    Side effects: 1 INSERT per order, 1 per line item, 4 per FSM transition,
    and one line in the ERP log.
    """
    if draft.customer.customer_id is None:
        raise RuntimeError("draft has no resolved customer_id; cannot persist.")
    # Pick an apiarist to attribute the draft to. In a real flow the agent
    # would resolve an owner from territory assignments; for the demo we
    # grab the first active user covering the customer's primary region.
    user_id = db.execute(
        text("SELECT id FROM users WHERE is_active = TRUE ORDER BY email LIMIT 1")
    ).scalar_one()

    order_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.execute(
        text(
            "INSERT INTO orders (id, customer_id, user_id, state, "
            "pollination_date, total_usd, source_email_id, created_at, updated_at) "
            "VALUES (:id, :cust, :user, :state, :pdate, :total, :src, :ts, :ts)"
        ),
        {
            "id": str(order_id),
            "cust": str(draft.customer.customer_id),
            "user": str(user_id),
            "state": OrderState.EXTRACTED.value,
            "pdate": draft.pollination_date,
            "total": draft.total_usd,
            "src": None,  # we don't persist synced_emails in the demo
            "ts": now,
        },
    )
    for li in draft.line_items:
        db.execute(
            text(
                "INSERT INTO order_line_items "
                "(id, order_id, product_id, service_id, sku, quantity, unit_price_usd) "
                "VALUES (:id, :oid, :pid, :sid, :sku, :q, :p)"
            ),
            {
                "id": str(uuid.uuid4()),
                "oid": str(order_id),
                "pid": str(li.product_id) if li.product_id else None,
                "sid": str(li.service_id) if li.service_id else None,
                "sku": li.sku,
                "q": li.quantity,
                "p": li.unit_price_usd,
            },
        )

    # FSM progression — a realistic abbreviated path.
    fsm_path: list[tuple[OrderState | None, OrderState]] = [
        (None, OrderState.EXTRACTED),
        (OrderState.EXTRACTED, OrderState.UNDER_REVIEW),
        (OrderState.UNDER_REVIEW, OrderState.APPROVED),
        (OrderState.APPROVED, OrderState.SCHEDULED),
    ]
    for from_s, to_s in fsm_path:
        db.execute(
            text(
                "INSERT INTO order_state_transitions "
                "(id, order_id, from_state, to_state, actor_id, transitioned_at, metadata) "
                "VALUES (:id, :oid, :fs, :ts_to, :actor, :ts, CAST('{}' AS jsonb))"
            ),
            {
                "id": str(uuid.uuid4()),
                "oid": str(order_id),
                "fs": from_s.value if from_s else None,
                "ts_to": to_s.value,
                "actor": str(user_id),
                "ts": now,
            },
        )
    db.execute(
        text(
            "UPDATE orders SET state = :s, updated_at = :ts WHERE id = :id"
        ),
        {"s": OrderState.SCHEDULED.value, "ts": now, "id": str(order_id)},
    )
    print(f"    persisted:    order_id={order_id}")
    print(f"    fsm path:     " + " → ".join(s.value for _, s in fsm_path))

    # ERP sync — a real call to the mock; surfaces as a line in /erp-sync-log/.
    erp = ERPClient(db=db)
    erp_order = {
        "customer_erp_no": draft.customer.erp_customer_no,
        "line_items": [
            {"sku": li.sku, "quantity": li.quantity} for li in draft.line_items
        ],
        "total_usd": str(draft.total_usd),
        "ship_via": "Flatbed",
    }
    resp = erp.submit_sales_order(erp_order)
    print(f"    erp synced:   so_no={resp.erp_so_no}  status={resp.status}")

    # Final FSM step into SYNCED_TO_ERP, recorded for history.
    db.execute(
        text(
            "INSERT INTO order_state_transitions "
            "(id, order_id, from_state, to_state, actor_id, transitioned_at, metadata) "
            "VALUES (:id, :oid, :fs, :ts_to, :actor, :ts, CAST(:meta AS jsonb))"
        ),
        {
            "id": str(uuid.uuid4()),
            "oid": str(order_id),
            "fs": OrderState.SCHEDULED.value,
            "ts_to": OrderState.SYNCED_TO_ERP.value,
            "actor": str(user_id),
            "ts": datetime.now(timezone.utc),
            "meta": f'{{"erp_so_no": "{resp.erp_so_no}"}}',
        },
    )
    db.execute(
        text("UPDATE orders SET state = :s, updated_at = :ts WHERE id = :id"),
        {
            "s": OrderState.SYNCED_TO_ERP.value,
            "ts": datetime.now(timezone.utc),
            "id": str(order_id),
        },
    )
    return order_id


# ---------------------------------------------------------------------------
# Step 5 — sales copilot Q&A + follow-up email
# ---------------------------------------------------------------------------


def copilot_qna(db: Session, draft: OrderDraft) -> None:
    """Search customer reports + draft a follow-up email."""
    provider = VectorSearchProvider(db=db)
    query = (
        f"recent inspection notes for {draft.customer.customer_name} — varroa + diseases"
    )
    hits = provider.search(query, top_k=3)
    print(f"    rag query:    {query!r}")
    print(f"    top hits:")
    for h in hits:
        print(f"      - score={h.score:.3f}  {h.content[:90]!r}")

    # Draft follow-up email directly, reusing the prompt helper from the
    # MCP server (keeps the logic single-sourced).
    assert draft.customer.customer_id is not None
    last_order = (
        db.execute(
            text(
                "SELECT state, total_usd, pollination_date, created_at "
                "FROM orders WHERE customer_id = :cid "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"cid": str(draft.customer.customer_id)},
        )
        .mappings()
        .first()
    )
    last_trip = (
        db.execute(
            text(
                "SELECT visit_date, hives_placed, varroa_count, narrative "
                "FROM customer_reports tr JOIN customer_sites cs "
                "  ON cs.id = tr.customer_site_id "
                "WHERE cs.customer_id = :cid "
                "ORDER BY tr.visit_date DESC LIMIT 1"
            ),
            {"cid": str(draft.customer.customer_id)},
        )
        .mappings()
        .first()
    )
    context = _format_email_context(
        draft.customer.customer_name or "(unknown)",
        "",
        last_order,
        last_trip,
    )
    llm = LLMClient()
    resp = llm.complete(
        f"Draft a warm, short follow-up email confirming the SCHEDULED order "
        f"(SO total ${draft.total_usd}) and noting the next site visit.\n\n"
        f"Context:\n{context}\n\n"
        "Return JSON with keys `subject` (≤ 80 chars) and `body_text` "
        "(2-3 short paragraphs, no signature).",
        max_tokens=600,
    )
    subject, body = _parse_subject_body(resp.text)
    print(f"    draft subject: {subject}")
    print(f"    draft body:")
    print(_indent(body, "      "))

    msg_id = send_mail(
        to=f"{(draft.customer.customer_name or 'customer').lower().replace(' ', '.')}@example.test",
        subject=subject,
        body_text=body,
    )
    print(f"    mailer logged: {msg_id}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run(account_id: str, message_id: str | None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _banner("Waggle — end-to-end demo")

    with session_scope() as db:
        _step("1/7", "Preflight")
        preflight(db)

        _step("2/7", "Inbound email")
        email = pull_email(account_id, message_id)

        _step("3/7", "LangGraph order extraction")
        draft = run_agent(db, email)

        _step("4/7", "Persist + FSM + ERP sync")
        order_id = persist_and_advance(db, draft)
        _ = order_id

        _step("5/7", "RAG search + follow-up email")
        copilot_qna(db, draft)

        _step("6/7", "Chat copilot — programmatic conversation turn")
        chat_copilot_turn(db, draft)

    # MCP step opens its own session_scope inside the tool implementation,
    # so we run it outside the orchestrator's session block.
    _step("7/7", "MCP tool call — find_customer via fastmcp Client")
    import asyncio
    asyncio.run(mcp_tool_demo(draft))

    _banner("Demo complete — every surface exercised end-to-end.")
    return 0


# ---------------------------------------------------------------------------
# Step 6 — chat copilot programmatic turn
# ---------------------------------------------------------------------------


def chat_copilot_turn(db: Session, draft: OrderDraft) -> None:
    """Open a conversation, post a turn through the orchestrator, persist.

    Mirrors what ``POST /copilot/turns`` does at the route layer but
    without standing up a webserver — we go straight through the
    orchestrator and write ``chat_turns`` rows directly.

    Side effects: 1 INSERT on ``conversations``, 2 INSERTs on
    ``chat_turns`` (user + assistant), 1-3 LLM calls.
    """
    from datetime import datetime, timezone

    from agents.copilot.account_resolver import build_account_index
    from agents.copilot.orchestrator import get_copilot_response
    from agents.order_extractor import _build_chat_model
    from app.config import get_settings

    settings = get_settings()
    models = {
        "router": _build_chat_model(settings.copilot_router_model),
        "main": _build_chat_model(settings.copilot_main_model),
        "report": _build_chat_model(settings.copilot_report_model),
    }
    account_index = build_account_index(db)

    customer_name = draft.customer.customer_name or "this customer"
    user_text = (
        f"What can you tell me about {customer_name}? Summarize their most "
        "recent visit notes and flag anything I should bring up next time."
    )

    # Conversation + user turn
    conv_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db.execute(
        text(
            "INSERT INTO conversations (id, active_account_id, created_at, updated_at) "
            "VALUES (:id, :acct, :ts, :ts)"
        ),
        {
            "id": str(conv_id),
            "acct": str(draft.customer.customer_id) if draft.customer.customer_id else None,
            "ts": now,
        },
    )
    user_turn_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO chat_turns (id, conversation_id, role, text, "
            "tool_calls, evidence_used, created_at) "
            "VALUES (:id, :cid, 'user', :t, "
            "CAST('[]' AS jsonb), CAST('[]' AS jsonb), :ts)"
        ),
        {"id": str(user_turn_id), "cid": str(conv_id), "t": user_text, "ts": now},
    )
    print(f"    user turn: {user_text!r}")

    # Orchestrator
    response = get_copilot_response(
        user_text=user_text,
        db=db,
        models=models,
        account_index=account_index,
        active_account_id=draft.customer.customer_id,
    )

    # Assistant turn — store the response text plus the recorded tool calls.
    import json as _json
    assistant_turn_id = uuid.uuid4()
    tool_calls_json = _json.dumps(
        [tc.model_dump(mode="json") for tc in response.tool_calls]
    )
    evidence_json = _json.dumps(list(response.evidence_used))
    db.execute(
        text(
            "INSERT INTO chat_turns (id, conversation_id, role, text, "
            "tool_calls, evidence_used, model, latency_ms, created_at) "
            "VALUES (:id, :cid, 'assistant', :t, "
            "CAST(:tc AS jsonb), CAST(:ev AS jsonb), :m, :lat, :ts)"
        ),
        {
            "id": str(assistant_turn_id),
            "cid": str(conv_id),
            "t": response.text,
            "tc": tool_calls_json,
            "ev": evidence_json,
            "m": response.model,
            "lat": response.latency_ms,
            "ts": datetime.now(timezone.utc),
        },
    )
    print(f"    conversation: {conv_id}")
    print(f"    model: {response.model}  · latency: {response.latency_ms} ms")
    print(f"    tool calls: {', '.join(tc.tool_name for tc in response.tool_calls) or '(none)'}")
    print(f"    assistant:")
    print(_indent(response.text, "      "))


# ---------------------------------------------------------------------------
# Step 7 — MCP tool call (via fastmcp in-process Client)
# ---------------------------------------------------------------------------


async def mcp_tool_demo(draft: OrderDraft) -> None:
    """Invoke a Sales-Copilot MCP tool against the running database.

    Uses fastmcp's in-process ``Client`` — the same protocol an external
    MCP client (Claude Desktop, Codex) would use, minus the stdio hop.
    Picks ``find_customer`` for a deterministic, no-LLM tool call.

    Side effects: SELECTs against the DB inside the tool. No writes.
    """
    from fastmcp import Client

    from mcp_servers.sales_copilot_server import server

    if not draft.customer.customer_name:
        print("    (no customer name on the draft; skipping MCP tool demo)")
        return

    async with Client(server) as client:
        result = await client.call_tool(
            "find_customer",
            {"name_or_alias": draft.customer.customer_name, "top_k": 3},
        )
    hits = result.data or []
    print(f"    tool: find_customer  · query: {draft.customer.customer_name!r}")
    print(f"    hits: {len(hits)}")
    for h in hits[:3]:
        print(
            f"      - {h.name:35s}  erp_no={h.erp_customer_no}  "
            f"segment={h.market_segment}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Walk the Waggle demo flow end-to-end."
    )
    parser.add_argument(
        "--account-id",
        default=DEFAULT_ACCOUNT_ID,
        help=f"Inbox account to poll (default: {DEFAULT_ACCOUNT_ID}).",
    )
    parser.add_argument(
        "--message-id",
        default=None,
        help="Specific message id to replay. Omit to grab the first unread.",
    )
    args = parser.parse_args(argv)
    return run(account_id=args.account_id, message_id=args.message_id)


if __name__ == "__main__":
    sys.exit(main())
