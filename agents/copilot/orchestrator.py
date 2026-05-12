"""Sales-copilot orchestrator — single chat-turn handler.

Pipeline per turn:

    1. Resolve any account hint in the user input via the prebuilt
       AccountIndex → customer_id (None if no match).
    2. Route intent via the LLM router → IntentRoute(Grounded).
    3. Dispatch on intent:
         query                 → build context, generate grounded answer
         log_customer_report   → extract structured CustomerReportDraft
         create_order          → run Phase A's order extractor on the text
         command / unknown     → placeholder responses (full impl in
                                 later phases).
    4. Bundle the result as a CopilotResponse with metrics + evidence.

Side effects: 1-3 LLM calls + a few SELECTs per turn. No DB writes here
— the route handler decides whether to persist a draft when the user
confirms.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from typing import Any, Callable, Literal

from langchain_core.language_models import BaseChatModel
from sqlalchemy.orm import Session


#: Stage names emitted via the optional ``on_step`` callback during one
#: chat turn. The streaming /copilot/turns SSE branch maps these to
#: front-end "thinking..." status updates.
StreamStage = Literal[
    "account_resolved",
    "intent_routed",
    "context_built",
    "answer_drafting",
    "answer_ready",
    "draft_ready",
]

#: Callback signature for orchestrator progress events. Implementations
#: should be fast and side-effect-only — the orchestrator does NOT await
#: them. The streaming SSE endpoint pushes events into an asyncio queue
#: from within this callback.
OnStepFn = Callable[[StreamStage, Any], None]

from agents.copilot.account_resolver import AccountIndex, resolve_account
from agents.copilot.context_builder import ContextBundle, build_context
from agents.copilot.conversation_schemas import (
    Citation,
    CopilotResponse,
    CustomerReportDraft,
    IntentRoute,
    ToolCallRecord,
)
from agents.copilot.intent_router import get_intent_route
from agents.copilot.tools import (
    draft_order_from_text,
    log_customer_report,
)
from agents.schemas import OrderDraft, ExtractionFailure

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small prompt templates for the answer-generation step
# ---------------------------------------------------------------------------


_QUERY_PROMPT = """\
You are Waggle, a sales copilot for a commercial-beekeeping business.
Answer the user's question using only the context provided. Be concise
(3-5 sentences). If the context doesn't contain the answer, say so —
do not invent facts.

User question:
{question}

Customer context:
{customer_block}

Recent customer reports:
{reports_block}

Retrieved evidence:
{rag_block}
"""


def _format_customer_block(bundle: ContextBundle) -> str:
    if bundle.customer_name is None:
        return "(no specific customer in context — answer generally)"
    erp = f" · {bundle.erp_customer_no}" if bundle.erp_customer_no else ""
    return f"{bundle.customer_name}{erp}"


def _format_reports_block(bundle: ContextBundle) -> str:
    if not bundle.recent_reports:
        return "(no recent reports available)"
    lines = []
    for rep in bundle.recent_reports:
        site = f" at {rep.site_name}" if rep.site_name else ""
        varroa = f" varroa={rep.varroa_count}" if rep.varroa_count is not None else ""
        lines.append(f"- {rep.visit_date}{site}{varroa}: {rep.excerpt[:200]}")
    return "\n".join(lines)


def _format_rag_block(bundle: ContextBundle) -> str:
    if not bundle.rag_chunks:
        return "(no retrieved evidence)"
    return "\n\n".join(f"- {c[:300]}" for c in bundle.rag_chunks)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def get_copilot_response(
    *,
    user_text: str,
    db: Session,
    models: dict[str, BaseChatModel],
    account_index: AccountIndex | None = None,
    active_account_id: uuid.UUID | None = None,
    on_step: OnStepFn | None = None,
) -> CopilotResponse:
    """Handle one chat turn end-to-end.

    ``models`` is a dict keyed by stage name:
        - ``router``   : fast model for IntentRoute
        - ``main``     : main answer-generation model
        - ``report``   : extraction model for log_customer_report
    Falls back to a single shared model if any key is missing.

    ``on_step`` is an optional progress callback fired at each major
    stage. The streaming SSE endpoint passes one in to drive live
    "thinking..." indicators; non-streaming callers can omit it. The
    callback is called synchronously from within this function — it
    must NOT block.

    Side effects: 1-3 LLM calls; SELECTs against ``db``; ``on_step``
    callbacks may have their own side effects (typically: pushing to a
    queue).
    """
    t0 = time.perf_counter()

    def _emit(stage: StreamStage, payload: Any) -> None:
        if on_step is not None:
            try:
                on_step(stage, payload)
            except Exception as exc:
                log.warning("on_step callback raised: %s", exc)

    router_model = models.get("router") or models.get("main")
    main_model = models.get("main") or models.get("router")
    report_model = models.get("report") or main_model

    if router_model is None or main_model is None:
        raise RuntimeError("orchestrator: at least one chat model required")

    # 1. Account resolution (deterministic, no LLM)
    customer_id: uuid.UUID | None = active_account_id
    if account_index is not None:
        candidates = resolve_account(user_text, account_index)
        if candidates:
            customer_id = candidates[0]
    _emit("account_resolved", {"customer_id": str(customer_id) if customer_id else None})

    # 2. Intent routing (1 LLM call)
    intent: IntentRoute = get_intent_route(user_text, model=router_model)
    _emit("intent_routed", {"intent": intent.intent})

    # If the router pulled an explicit hint, override our resolution with it.
    if intent.target_account_hint and account_index is not None:
        ids = resolve_account(intent.target_account_hint, account_index)
        if ids:
            customer_id = ids[0]

    tool_calls: list[ToolCallRecord] = []
    citations: list[Citation] = []
    answer_text: str
    evidence_used: list[str] = list(intent.evidence)
    pending_order: OrderDraft | None = None
    pending_report: CustomerReportDraft | None = None

    # 3. Dispatch on intent
    if intent.intent == "query":
        bundle = build_context(
            query=user_text, customer_id=customer_id, db=db, top_k=5
        )
        _emit("context_built", {
            "reports": len(bundle.recent_reports),
            "chunks": len(bundle.rag_chunks),
        })
        prompt = _QUERY_PROMPT.format(
            question=user_text,
            customer_block=_format_customer_block(bundle),
            reports_block=_format_reports_block(bundle),
            rag_block=_format_rag_block(bundle),
        )
        _emit("answer_drafting", {"intent": "query"})
        t_before = time.perf_counter()
        ai_msg = main_model.invoke(prompt)
        latency = int((time.perf_counter() - t_before) * 1000)
        answer_text = _ai_text(ai_msg)
        _emit("answer_ready", {"latency_ms": latency})
        tool_calls.append(_record(
            "build_context",
            {"customer_id": str(customer_id) if customer_id else None,
             "query": user_text},
            f"customer={bundle.customer_name} reports={len(bundle.recent_reports)} "
            f"chunks={len(bundle.rag_chunks)} tokens~{bundle.estimated_tokens}",
            latency_ms=latency,
        ))
        for rep in bundle.recent_reports[:3]:
            citations.append(Citation(
                kind="customer_report",
                id=uuid.uuid4(),  # placeholder; real impl pulls report_id
                label=f"Report {rep.visit_date}",
                excerpt=rep.excerpt[:200],
            ))

    elif intent.intent == "log_customer_report":
        _emit("answer_drafting", {"intent": "log_customer_report"})
        report_draft: CustomerReportDraft = log_customer_report(
            text_in=user_text, model=report_model or main_model,
        )
        _emit("draft_ready", {"kind": "report"})
        evidence_used.extend(report_draft.evidence)
        pending_report = report_draft
        tool_calls.append(_record(
            "log_customer_report", {"text": user_text[:200]},
            f"varroa_pct={report_draft.varroa_pct} "
            f"observations={len(report_draft.observations)} "
            f"actions={len(report_draft.action_items)}",
            latency_ms=0,
        ))
        answer_text = (
            "I extracted a draft customer report from that. "
            f"It mentions {len(report_draft.observations)} observation(s) "
            f"and {len(report_draft.action_items)} follow-up item(s). "
            "Review and confirm to save."
        )

    elif intent.intent == "create_order":
        _emit("answer_drafting", {"intent": "create_order"})
        order_result = draft_order_from_text(db=db, text_in=user_text)
        if isinstance(order_result, OrderDraft):
            _emit("draft_ready", {"kind": "order"})
            evidence_used.extend(order_result.customer_evidence)
            pending_order = order_result
            tool_calls.append(_record(
                "draft_order_from_text", {"text": user_text[:200]},
                f"customer={order_result.customer.customer_name} "
                f"items={len(order_result.line_items)} "
                f"total=${order_result.total_usd}",
                latency_ms=0,
            ))
            answer_text = (
                f"I drafted an order for {order_result.customer.customer_name} "
                f"with {len(order_result.line_items)} line item(s), "
                f"total ${order_result.total_usd:,.2f}. Review and confirm to save."
            )
        else:
            assert isinstance(order_result, ExtractionFailure)
            answer_text = (
                "I couldn't draft a complete order from that — "
                f"the agent stopped at the {order_result.stage} stage. "
                "Try rephrasing, or check the customer name."
            )

    elif intent.intent == "command":
        # Placeholder: full command-handling (price changes, status updates)
        # lands in a follow-up phase with proper validation + audit.
        answer_text = (
            "Direct field updates aren't enabled in this demo yet. "
            "I can help you draft an order or look up a customer instead."
        )

    else:  # unknown
        answer_text = (
            "I'm not sure what you'd like me to do. "
            "I can answer questions about customers, draft orders, or "
            "log a visit report — try one of those?"
        )

    # 4. Wrap in CopilotResponse
    total_latency = int((time.perf_counter() - t0) * 1000)
    return CopilotResponse(
        text=answer_text,
        citations=citations,
        tool_calls=tool_calls,
        evidence_used=evidence_used,
        model=getattr(main_model, "model_name", "unknown"),
        tokens_input=0,    # token accounting wired in step 17 budget pass
        tokens_output=0,
        cost_usd=Decimal("0"),
        latency_ms=total_latency,
        pending_order_draft=pending_order,
        pending_report_draft=pending_report,
        resolved_customer_id=customer_id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ai_text(ai_msg: Any) -> str:
    """Coerce a LangChain AI-message-or-string into plain text."""
    if hasattr(ai_msg, "content"):
        c = ai_msg.content
        if isinstance(c, list):  # blocks
            parts = []
            for block in c:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(str(block.get("text", "")))
            return "".join(parts)
        return str(c)
    return str(ai_msg)


def _record(
    name: str, args: dict[str, Any], result_excerpt: str, *, latency_ms: int
) -> ToolCallRecord:
    """Build a ToolCallRecord for the chat-turn log."""
    return ToolCallRecord(
        tool_name=name,
        arguments=args,
        result_excerpt=result_excerpt[:4096],
        latency_ms=latency_ms,
        tokens=0,
    )


__all__ = ["get_copilot_response"]
