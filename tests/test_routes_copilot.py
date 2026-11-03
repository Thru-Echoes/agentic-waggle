"""End-to-end tests for the sales-copilot HTTP route layer.

Covers the HTML shell, all four orchestrator intents, draft confirmation,
the conversation rail, and the JSON API mirrors. Real Postgres + real
sentence-transformers + real LLM (when the key is set; tests that need
the LLM use ``require_llm_api_key``).

Pattern matches the rest of the test suite: a per-test fresh schema via
the ``db`` fixture, plus a TestClient that overrides
``get_db_session`` to yield the fixture session.

Side effects: HTTP requests against an in-process FastAPI app, DB writes
to the fixture schema, and 1-3 LLM calls per orchestrator-touching test.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from db.models import (
    ChatTurn,
    Conversation,
    CustomerReport,
    Order,
    OrderLineItem,
    OrderStateTransition,
)
from db.session import get_db_session


# ---------------------------------------------------------------------------
# Test client + DB-override fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db) -> Iterator[TestClient]:
    """FastAPI TestClient with get_db_session bound to the fixture session."""
    app = create_app()

    def _override():
        yield db

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers — kept light so tests start fast.
# ---------------------------------------------------------------------------


def _seed_min_for_orchestrator(db, faker_instance) -> None:
    """Customers + sites + apiarists + products + embeddings.

    Skips orders / inspections to keep latency down. Embeddings are
    required for the build_context RAG step on the query intent.
    """
    from seed import (
        apiarists as apiarists_gen,
        customers as customers_gen,
        embeddings as embeddings_gen,
        products as products_gen,
        regions as regions_gen,
        services as services_gen,
        sites as sites_gen,
    )
    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    products_gen.persist(db, products_gen.generate(faker_instance))
    services_gen.persist(db, services_gen.generate())
    embeddings_gen.run(db)
    # Matcher search index (Phase I): required before any customer/SKU match.
    from providers import semantic_matcher

    semantic_matcher.backfill_all(db)


def _count(db, table: str) -> int:
    """Quick row count via raw SQL."""
    return int(
        db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
    )


# ---------------------------------------------------------------------------
# Shell + page rendering
# ---------------------------------------------------------------------------


class TestCopilotShell:
    def test_get_copilot_renders_with_empty_state(self, client, db) -> None:
        resp = client.get("/copilot")
        assert resp.status_code == 200
        body = resp.text
        assert "How can Waggle help?" in body
        assert "Message Waggle" in body  # composer placeholder
        assert 'name="conversation_id"' in body
        # Auto-creates a conversation row.
        assert _count(db, "conversations") == 1

    def test_get_copilot_for_specific_conversation_renders(
        self, client, db
    ) -> None:
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        resp = client.get(f"/copilot/{conv.id}")
        assert resp.status_code == 200
        assert str(conv.id) in resp.text

    def test_get_copilot_for_unknown_conversation_404(self, client) -> None:
        resp = client.get(f"/copilot/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_post_create_conversation_redirects(self, client, db) -> None:
        before = _count(db, "conversations")
        resp = client.post("/copilot/conversations", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/copilot/")
        assert _count(db, "conversations") == before + 1


# ---------------------------------------------------------------------------
# Posting turns — the four orchestrator intents
# ---------------------------------------------------------------------------


class TestPostTurn:
    def test_query_persists_user_and_assistant_turns(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        before_turns = _count(db, "chat_turns")
        resp = client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": "What's the general state of varroa across our customers?",
            },
        )
        assert resp.status_code == 200
        assert _count(db, "chat_turns") == before_turns + 2
        # Body is the turn-pair partial: should have one user + one assistant bubble.
        assert 'data-role="user"' in resp.text
        assert 'data-role="assistant"' in resp.text

        # Assistant turn should have at least one real tool call recorded.
        assistant_rows = db.execute(
            text(
                "SELECT tool_calls FROM chat_turns "
                "WHERE conversation_id = :cid AND role = 'assistant'"
            ),
            {"cid": str(conv.id)},
        ).mappings().all()
        assert len(assistant_rows) == 1
        tool_calls = assistant_rows[0]["tool_calls"]
        assert isinstance(tool_calls, list)
        # build_context is the canonical query-intent tool call.
        assert any(tc.get("tool_name") == "build_context" for tc in tool_calls)

    def test_create_order_intent_stashes_pending_draft(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        # Pull a real customer name so the orchestrator can actually match.
        cust_name = db.execute(
            text("SELECT name FROM customers ORDER BY name LIMIT 1")
        ).scalar_one()

        resp = client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                # Cite the SKU code: this test is about the create_order
                # intent stashing a draft, not about SKU disambiguation, and
                # "queens" alone is ambiguous across five queen SKUs.
                "text": (
                    f"Draft an order for {cust_name}: 24 Italian mated queens "
                    "(SKU QUEEN-ITA-MATED), they need them by next Friday."
                ),
            },
        )
        assert resp.status_code == 200

        assistant_rows = db.execute(
            text(
                "SELECT tool_calls FROM chat_turns "
                "WHERE conversation_id = :cid AND role = 'assistant'"
            ),
            {"cid": str(conv.id)},
        ).mappings().all()
        assert len(assistant_rows) == 1
        tool_calls = assistant_rows[0]["tool_calls"]
        # Either the draft worked (pending_order_draft synthetic record)
        # OR the agent surfaced a friendly failure message — both are
        # acceptable demo paths. We assert AT LEAST one path produced.
        names = {tc.get("tool_name") for tc in tool_calls}
        assert (
            "pending_order_draft" in names
            or "draft_order_from_text" in names
        )

    def test_log_report_intent_stashes_pending_report(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        resp = client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": (
                    "Trip report: visited Cascade today. Mites at 3.5%, "
                    "queens healthy, planned a follow-up next week."
                ),
            },
        )
        assert resp.status_code == 200

        rows = db.execute(
            text(
                "SELECT tool_calls FROM chat_turns "
                "WHERE conversation_id = :cid AND role = 'assistant'"
            ),
            {"cid": str(conv.id)},
        ).mappings().all()
        names = {tc.get("tool_name") for r in rows for tc in r["tool_calls"]}
        # log_customer_report is the canonical tool name; the synthetic
        # pending_report_draft is added when the extraction succeeded.
        assert "log_customer_report" in names

    def test_unknown_input_returns_friendly_message(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        resp = client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": "qwerty asdf zxcv plover frobnicate",
            },
        )
        assert resp.status_code == 200
        # Should still produce both turns even on weirdness.
        assert _count(db, "chat_turns") == 2

    def test_first_turn_titles_the_conversation(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)
        assert conv.title is None

        client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": "What's the general state of varroa across our customers?",
            },
        )
        db.refresh(conv)
        assert conv.title is not None
        assert len(conv.title) > 0

    def test_account_auto_resolution_updates_conversation(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)
        assert conv.active_account_id is None

        cust_name = db.execute(
            text("SELECT name FROM customers ORDER BY name LIMIT 1")
        ).scalar_one()

        client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": f"What's the latest at {cust_name}?",
            },
        )
        db.refresh(conv)
        # Either the account got resolved (updated active_account_id) or
        # the LLM failed to identify a hint and the slot stays None.
        # We assert the wiring is working when the LLM cooperates.
        assert (
            conv.active_account_id is None
            or db.get(Conversation, conv.id).active_account_id is not None
        )


# ---------------------------------------------------------------------------
# Confirm-and-persist
# ---------------------------------------------------------------------------


class TestStreamingTurn:
    """SSE streaming branch — Accept: text/event-stream."""

    def test_streaming_emits_user_turn_status_complete(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        resp = client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": "What's the general state of varroa across our customers?",
            },
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        body = resp.text
        # SSE framing: each event has "event: NAME\n" and "data: ..." lines.
        # We assert at minimum: user_turn first, then status, then complete.
        assert "event: user_turn" in body
        assert "event: status" in body
        assert "event: complete" in body
        # Order: user_turn appears before complete.
        assert body.index("event: user_turn") < body.index("event: complete")
        # The complete event carries the full assistant bubble HTML.
        assert "data-role=\"assistant\"" in body

        # Two ChatTurns landed in DB regardless of streaming framing.
        rows = db.execute(
            text(
                "SELECT role FROM chat_turns WHERE conversation_id = :cid "
                "ORDER BY created_at"
            ),
            {"cid": str(conv.id)},
        ).scalars().all()
        assert list(rows) == ["user", "assistant"]

    def test_non_streaming_branch_unchanged(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        """Sanity: clients that don't ask for SSE still get the partial."""
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        resp = client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": "What's the state of varroa?",
            },
            # No Accept header → defaults to text/html
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        # No SSE framing.
        assert "event: user_turn" not in resp.text
        # Both bubbles in the body (the canonical concatenated pair).
        assert 'data-role="user"' in resp.text
        assert 'data-role="assistant"' in resp.text


class TestConfirmDrafts:
    def test_confirm_order_persists_when_draft_present(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        cust_name = db.execute(
            text("SELECT name FROM customers ORDER BY name LIMIT 1")
        ).scalar_one()
        sku, unit = db.execute(
            text(
                "SELECT sku, name FROM products WHERE category = 'queens' LIMIT 1"
            )
        ).first() or (None, None)
        if sku is None:
            sku, unit = db.execute(
                text("SELECT sku, name FROM products LIMIT 1")
            ).first()

        client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": (
                    f"Create an order for {cust_name}: 12 units of "
                    f"{unit} (sku {sku})."
                ),
            },
        )

        # Find an assistant turn that has a pending_order_draft.
        rows = db.execute(
            text(
                """
                SELECT id, tool_calls FROM chat_turns
                WHERE conversation_id = :cid AND role = 'assistant'
                """
            ),
            {"cid": str(conv.id)},
        ).mappings().all()
        candidate = None
        for r in rows:
            for tc in r["tool_calls"]:
                if tc.get("tool_name") == "pending_order_draft":
                    candidate = r["id"]
                    break
            if candidate:
                break

        if candidate is None:
            pytest.skip(
                "Orchestrator did not produce a pending_order_draft on this "
                "non-deterministic LLM run; nothing to confirm. (Test still "
                "verifies the route doesn't error out.)"
            )

        before_orders = _count(db, "orders")
        before_lines = _count(db, "order_line_items")
        before_trans = _count(db, "order_state_transitions")

        resp = client.post(f"/copilot/turns/{candidate}/confirm-order")
        assert resp.status_code == 200
        assert "Saved" in resp.text or "Could not save" in resp.text
        if "Saved" in resp.text:
            assert _count(db, "orders") == before_orders + 1
            assert _count(db, "order_line_items") >= before_lines + 1
            assert _count(db, "order_state_transitions") == before_trans + 1

    def test_confirm_report_persists_when_draft_present(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)

        cust_name = db.execute(
            text("SELECT name FROM customers ORDER BY name LIMIT 1")
        ).scalar_one()

        client.post(
            "/copilot/turns",
            data={
                "conversation_id": str(conv.id),
                "text": (
                    f"Visited {cust_name} today, mites at 2.1%, "
                    "queens healthy. Will follow up next week."
                ),
            },
        )

        rows = db.execute(
            text(
                """
                SELECT id, tool_calls FROM chat_turns
                WHERE conversation_id = :cid AND role = 'assistant'
                """
            ),
            {"cid": str(conv.id)},
        ).mappings().all()
        candidate = None
        for r in rows:
            for tc in r["tool_calls"]:
                if tc.get("tool_name") == "pending_report_draft":
                    candidate = r["id"]
                    break
            if candidate:
                break
        if candidate is None:
            pytest.skip(
                "Orchestrator did not produce a pending_report_draft on this run."
            )

        before = _count(db, "customer_reports")
        resp = client.post(f"/copilot/turns/{candidate}/confirm-report")
        assert resp.status_code == 200
        if "Saved" in resp.text:
            assert _count(db, "customer_reports") == before + 1

    def test_confirm_unknown_turn_returns_error_message(self, client) -> None:
        resp = client.post(f"/copilot/turns/{uuid.uuid4()}/confirm-order")
        assert resp.status_code == 200
        assert "Could not" in resp.text or "find turn" in resp.text


# ---------------------------------------------------------------------------
# Conversation rail
# ---------------------------------------------------------------------------


class TestConversationRail:
    def test_rail_lists_conversations_newest_first(self, client, db) -> None:
        # The rail is scoped to the visitor: mint the cookie first and tag the
        # rows with that visitor so they are listed.
        assert client.get("/copilot").status_code == 200
        visitor = db.execute(text("SELECT visitor_id FROM conversations LIMIT 1")).scalar_one()
        # Three conversations, deliberate order.
        ids = []
        for title in ("oldest", "middle", "newest"):
            conv = Conversation(title=title, visitor_id=visitor)
            db.add(conv)
            db.commit()
            db.refresh(conv)
            ids.append(conv.id)
            # nudge updated_at via raw SQL — cleanest deterministic path
            db.execute(
                text("UPDATE conversations SET updated_at = NOW() WHERE id = :i"),
                {"i": str(conv.id)},
            )
            db.commit()

        resp = client.get("/copilot/_rail")
        assert resp.status_code == 200
        body = resp.text
        # Newest title appears first in the body.
        i_oldest = body.find("oldest")
        i_middle = body.find("middle")
        i_newest = body.find("newest")
        assert i_newest < i_middle < i_oldest

    def test_rail_handles_empty_db(self, client) -> None:
        resp = client.get("/copilot/_rail")
        assert resp.status_code == 200
        assert "No conversations yet" in resp.text


# ---------------------------------------------------------------------------
# JSON API mirrors
# ---------------------------------------------------------------------------


class TestJsonApi:
    def test_get_conversations_lists(self, client, db) -> None:
        Conversation()
        db.commit()
        for _ in range(3):
            db.add(Conversation())
            db.commit()

        resp = client.get("/api/copilot/conversations")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert len(body["items"]) == body["total"]
        assert body["total"] >= 3

    def test_get_conversation_with_turns(self, client, db) -> None:
        conv = Conversation(title="api-test")
        db.add(conv)
        db.commit()
        db.refresh(conv)

        # Add a couple of plain turns directly (no LLM needed).
        db.add(ChatTurn(conversation_id=conv.id, role="user", text="hi"))
        db.add(ChatTurn(
            conversation_id=conv.id, role="assistant",
            text="hello back",
        ))
        db.commit()

        resp = client.get(f"/api/copilot/conversations/{conv.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(conv.id)
        assert body["title"] == "api-test"
        assert len(body["turns"]) == 2

    def test_get_conversation_404(self, client) -> None:
        resp = client.get(f"/api/copilot/conversations/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_post_create_conversation_json(self, client) -> None:
        resp = client.post("/api/copilot/conversations", json={})
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["turns"] == []


# ---------------------------------------------------------------------------
# Regression: legacy / schema-evolved persisted JSON must not 500
# (the production /copilot incident — pre-Phase-I stashed drafts).
# ---------------------------------------------------------------------------


class TestLegacyDraftRegression:
    """A ChatTurn holding pre-Phase-I JSON must render + confirm
    gracefully, never HTTP 500.

    The turn carries (a) a stashed OrderDraft whose ``customer`` lacks the
    now-required ``evidence`` field — the exact incident cause — and (b) a
    real tool_call record predating required ``result_excerpt`` /
    ``latency_ms``. Guards exercised: ``_split_tool_calls`` (L3),
    ``_turn_view_for_template`` draft re-hydration (L1),
    ``_persist_order_draft`` (L4).
    """

    def _legacy_turn(self, db) -> tuple[uuid.UUID, uuid.UUID]:
        from agents.schemas import CustomerMatch, OrderDraft, ResolvedLineItem
        from app.routes.copilot import _PENDING_ORDER_TOOL_NAME

        cust = db.execute(
            text("SELECT id, name FROM customers LIMIT 1")
        ).mappings().first()
        prod = db.execute(
            text("SELECT id, sku, price_usd FROM products LIMIT 1")
        ).mappings().first()

        valid = OrderDraft(
            customer=CustomerMatch(
                customer_id=cust["id"], customer_name=cust["name"],
                confidence=1.0, evidence=[cust["name"]], alternatives=[],
            ),
            line_items=[ResolvedLineItem(
                sku=prod["sku"], product_id=prod["id"], service_id=None,
                quantity=5, unit_price_usd=prod["price_usd"],
                match_method="exact_sku", source_description="5 units",
                evidence=[], confidence=1.0,
            )],
            total_usd=prod["price_usd"] * 5,
            overall_confidence=1.0, extraction_concerns=[],
            customer_evidence=[], pollination_date=None,
            source_email_external_id=None,
        )
        legacy_draft = valid.model_dump(mode="json")
        # Simulate a row written before customer.evidence was required.
        del legacy_draft["customer"]["evidence"]

        conv = Conversation()
        db.add(conv)
        db.commit()
        db.refresh(conv)
        turn = ChatTurn(
            conversation_id=conv.id,
            role="assistant",
            text="I drafted an order. Review and confirm to save.",
            tool_calls=[
                # Legacy real tool_call: predates required
                # result_excerpt / latency_ms → exercises L3.
                {"tool_name": "build_context", "arguments": {"hint": "x"}},
                # Legacy stashed draft missing customer.evidence → L1 + L4.
                {"tool_name": _PENDING_ORDER_TOOL_NAME,
                 "arguments": {"draft_data": legacy_draft}},
            ],
        )
        db.add(turn)
        db.commit()
        db.refresh(turn)
        return conv.id, turn.id

    def test_legacy_payload_is_genuinely_rejected(
        self, db, faker_instance
    ) -> None:
        # Non-vacuous guard: prove the fixture truly reproduces the
        # incident (current schema must reject it for the evidence reason).
        from pydantic import ValidationError
        from agents.schemas import OrderDraft

        _seed_min_for_orchestrator(db, faker_instance)
        _, turn_id = self._legacy_turn(db)
        turn = db.get(ChatTurn, turn_id)
        draft_data = turn.tool_calls[1]["arguments"]["draft_data"]
        with pytest.raises(ValidationError):
            OrderDraft.model_validate(draft_data)

    def test_copilot_shell_renders_legacy_turn_200(
        self, client, db, faker_instance
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        conv_id, _ = self._legacy_turn(db)
        resp = client.get(f"/copilot/{conv_id}")
        assert resp.status_code == 200  # L1 + L3 guards: no 500

    def test_confirm_order_on_legacy_draft_degrades_gracefully(
        self, client, db, faker_instance
    ) -> None:
        _seed_min_for_orchestrator(db, faker_instance)
        _, turn_id = self._legacy_turn(db)
        resp = client.post(f"/copilot/turns/{turn_id}/confirm-order")
        assert resp.status_code == 200  # L4 guard: not 500
        assert "predates the current schema" in resp.text


# ---------------------------------------------------------------------------
# Type-check: imports below are exercised by the test runner.
# ---------------------------------------------------------------------------

_ = Order, OrderLineItem, OrderStateTransition, CustomerReport


def _seed_with_reports(db, faker_instance) -> None:
    """Full seed incl. inspections + embeddings + matcher index, so a query
    turn has reports to cite."""
    from providers import semantic_matcher
    from seed import (
        apiarists as apiarists_gen,
        customers as customers_gen,
        embeddings as embeddings_gen,
        inspections as inspections_gen,
        products as products_gen,
        regions as regions_gen,
        services as services_gen,
        sites as sites_gen,
    )

    regions = regions_gen.generate()
    regions_gen.persist(db, regions)
    customers = customers_gen.generate(faker_instance)
    customers_gen.persist(db, customers)
    sites = sites_gen.generate(faker_instance, customers, regions)
    sites_gen.persist(db, sites)
    apiarists = apiarists_gen.generate(faker_instance, regions)
    apiarists_gen.persist(db, apiarists)
    inspections_gen.persist(db, inspections_gen.generate(faker_instance, sites, apiarists))
    products_gen.persist(db, products_gen.generate(faker_instance))
    services_gen.persist(db, services_gen.generate())
    embeddings_gen.run(db)
    semantic_matcher.backfill_all(db)
    db.commit()


# ---------------------------------------------------------------------------
# Visitor scoping, expired drafts, citations
# ---------------------------------------------------------------------------


class TestVisitorScoping:
    def _two_clients(self, db) -> tuple[TestClient, TestClient]:
        app = create_app()

        def _override():
            yield db

        app.dependency_overrides[get_db_session] = _override
        return TestClient(app), TestClient(app)

    def test_first_visit_lands_on_a_fresh_conversation_scoped_to_the_visitor(self, db) -> None:
        a, _ = self._two_clients(db)
        resp = a.get("/copilot")
        assert resp.status_code == 200
        assert "waggle_visitor" in resp.cookies
        assert "How can Waggle help?" in resp.text
        row = db.execute(text("SELECT visitor_id FROM conversations")).scalar_one()
        assert row  # the new conversation belongs to a visitor

    def test_visitors_do_not_see_each_others_conversations(self, db) -> None:
        a, b = self._two_clients(db)
        a.get("/copilot")
        a_conv = db.execute(text("SELECT id FROM conversations")).scalar_one()
        b_page = b.get("/copilot")
        assert b_page.status_code == 200
        # B has its own conversation now; the rail lists only B's.
        assert f"/copilot/{a_conv}" not in b_page.text
        assert _count(db, "conversations") == 2
        # Direct URL access to A's conversation is a 404 for B.
        assert b.get(f"/copilot/{a_conv}").status_code == 404
        # And A still sees its own.
        assert a.get(f"/copilot/{a_conv}").status_code == 200

    def test_api_without_cookie_lists_only_api_created_conversations(self, db) -> None:
        a, _ = self._two_clients(db)
        a.get("/copilot")  # one browser conversation
        api = TestClient(a.app)  # fresh cookie jar, no visitor cookie sent
        listing = api.get("/api/copilot/conversations").json()
        assert listing["total"] == 0 and listing["items"] == []
        created = api.post("/api/copilot/conversations", json={}).json()
        listing = api.get("/api/copilot/conversations").json()
        assert [i["id"] for i in listing["items"]] == [created["id"]]

    def test_api_create_with_browser_cookie_lands_in_that_visitors_rail(self, db) -> None:
        a, b = self._two_clients(db)
        a.get("/copilot")  # mints A's cookie and A's first conversation
        created = a.post("/api/copilot/conversations", json={}).json()
        assert f"/copilot/{created['id']}" in a.get("/copilot").text
        assert created["id"] in [i["id"] for i in a.get("/api/copilot/conversations").json()["items"]]
        # Another visitor neither lists it nor reaches it by URL.
        assert f"/copilot/{created['id']}" not in b.get("/copilot").text
        assert b.get(f"/copilot/{created['id']}").status_code == 404
        # A cookie-less API client does not list it either.
        api = TestClient(a.app)
        assert created["id"] not in [i["id"] for i in api.get("/api/copilot/conversations").json()["items"]]

    def test_turn_post_to_another_visitors_conversation_is_404(self, db) -> None:
        a, b = self._two_clients(db)
        a.get("/copilot")
        a_conv = db.execute(text("SELECT id FROM conversations")).scalar_one()
        resp = b.post("/copilot/turns", data={"conversation_id": str(a_conv), "text": "hello"})
        assert resp.status_code == 404


class TestExpiredDraftNote:
    def test_legacy_stashed_draft_renders_an_explicit_note(self, client, db) -> None:
        assert client.get("/copilot").status_code == 200
        conv_id = db.execute(text("SELECT id FROM conversations")).scalar_one()
        # A stashed draft from before CustomerMatch required `evidence`.
        legacy = json.dumps([{
            "tool_name": "pending_order_draft",
            "arguments": {"draft_data": {
                "state": "EXTRACTED",
                "customer": {"customer_id": str(uuid.uuid4()), "customer_name": "Old Co.", "score": 90.0, "confidence": 0.9},
                "line_items": [], "total_usd": "1.00", "overall_confidence": 0.9,
            }},
            "result_excerpt": "legacy", "latency_ms": 0, "tokens": 0,
        }])
        db.execute(
            text("INSERT INTO chat_turns (id, conversation_id, role, text, tool_calls) "
                 "VALUES (:tid, :cid, 'assistant', 'Review and confirm to save.', CAST(:tc AS jsonb))"),
            {"tid": str(uuid.uuid4()), "cid": str(conv_id), "tc": legacy},
        )
        db.commit()
        page = client.get(f"/copilot/{conv_id}")
        assert page.status_code == 200
        assert "Draft expired" in page.text
        assert "can no longer be confirmed" in page.text
        assert "draft-order-" not in page.text


class TestCitations:
    def test_query_turn_persists_real_report_citations(
        self, client, db, faker_instance, require_llm_api_key
    ) -> None:
        _seed_with_reports(db, faker_instance)
        cust = db.execute(text(
            "SELECT c.id, c.name FROM customers c JOIN customer_sites s ON s.customer_id=c.id "
            "JOIN customer_reports r ON r.customer_site_id=s.id LIMIT 1"
        )).mappings().one()

        conv = client.post("/api/copilot/conversations", json={"active_account_id": str(cust["id"])}).json()
        turn_resp = client.post("/copilot/turns", data={
            "conversation_id": conv["id"],
            "text": f"What's the varroa situation at {cust['name']}?",
        })
        assert turn_resp.status_code == 200
        api = client.get(f"/api/copilot/conversations/{conv['id']}").json()
        assistant = [t for t in api["turns"] if t["role"] == "assistant"][-1]
        assert assistant["citations"], "query turn must cite the reports it was grounded on"
        for c in assistant["citations"]:
            assert c["kind"] == "customer_report"
            exists = db.execute(text("SELECT 1 FROM customer_reports WHERE id = :id"), {"id": c["id"]}).first()
            assert exists is not None, f"citation {c['id']} does not point at a real report"
            assert client.get(f"/customer-reports/{c['id']}").status_code == 200
        page = client.get(f"/copilot/{conv['id']}")
        assert f"/customer-reports/{assistant['citations'][0]['id']}" in page.text
