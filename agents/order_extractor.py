"""LangGraph-backed pipeline that turns an inbound email into an `OrderDraft`.

Flow (StateGraph nodes, routed by the output of each):

    classify_intent
        ↓ (order_request)
    extract_customer_reference
        ↓
    match_customer (rapidfuzz against `customers`)
        ↓
    extract_line_items (LLM with `.with_structured_output(list[RequestedLineItem])`)
        ↓
    resolve_skus (rapidfuzz + fallback LLM against `products` + `services`)
        ↓
    assemble_draft
        ↓ END

At every node, a failure short-circuits to `END` with an `ExtractionFailure`
populated — no silent success. The caller (route handler or MCP tool) sees
`ExtractionResult = OrderDraft | ExtractionFailure` and decides what to do.

Side effects:
    * SELECTs against the provided Session at `match_customer` and `resolve_skus`.
    * One LLM call per node that uses the chat model (classify, extract_customer,
      extract_line_items, potentially resolve_skus for LLM-guess fallback).
"""
# pyright: reportMissingImports=false
# pyright: reportTypedDictNotRequiredAccess=false
# Rationale: LangGraph nodes read fields populated by prior nodes in the DAG.
# The NotRequired TypedDict pattern is the canonical LangGraph shape for this
# (official examples do the same). Runtime safety is guaranteed by graph
# topology; pyright has no way to prove that statically.
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Literal, NotRequired, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from agents.customer_matcher import find_customer_match
from agents.schemas import (
    CustomerMatch,
    CustomerReference,
    EmailInput,
    ExtractionFailure,
    ExtractionResult,
    IntentClassification,
    OrderDraft,
    RequestedLineItem,
    ResolvedLineItem,
)
from agents.sku_resolver import _load_catalog, resolve_line_item, resolve_via_llm

log = logging.getLogger(__name__)


class _State(TypedDict):
    """Accumulated graph state. Required fields are set at entry; NotRequired
    fields are populated progressively by nodes. `result` is set exactly once
    at the terminal node (either assemble_draft or an early failure)."""

    email: EmailInput
    db: Session  # stashed here so nodes can query without dependency injection
    models: dict[str, BaseChatModel]  # stage_name -> per-stage chat model
    notes: list[str]
    intent: NotRequired[IntentClassification]
    customer_ref: NotRequired[CustomerReference]
    customer_match: NotRequired[CustomerMatch]
    requested_items: NotRequired[list[RequestedLineItem]]
    resolved_items: NotRequired[list[ResolvedLineItem]]
    failure: NotRequired[ExtractionFailure]
    result: NotRequired[ExtractionResult]


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


def _build_chat_model(model_name: str | None = None) -> BaseChatModel:
    """Return a configured LangChain chat model.

    When ``model_name`` is provided it overrides the configured default —
    used by the order extractor to assign different models to each stage
    (see Settings.extract_*_model fields).

    Reuses ``providers.llm.resolve_provider()`` so the provider decision
    is single-sourced. Raises ``LLMConfigurationError`` if no key is set.
    """
    from app.config import get_settings
    from providers.llm import resolve_provider

    settings = get_settings()
    provider = resolve_provider()  # raises LLMConfigurationError on no keys

    if provider == "openai":
        openai_kwargs: dict[str, Any] = {
            "model": model_name or settings.openai_model,
            "temperature": 0.0,
            "timeout": 60,
            "max_retries": 2,
        }
        return ChatOpenAI(**openai_kwargs)
    anthropic_kwargs: dict[str, Any] = {
        "model": model_name or settings.anthropic_model,
        "temperature": 0.0,
        "timeout": 60,
        "max_retries": 2,
    }
    return ChatAnthropic(**anthropic_kwargs)


def _build_stage_models() -> dict[str, BaseChatModel]:
    """Build one model per extraction stage, keyed by stage name.

    Stages: ``intent``, ``customer``, ``items``, ``resolver_fallback``.
    Each pulls its model name from Settings so per-stage tuning happens
    in one place.
    """
    from app.config import get_settings

    s = get_settings()
    return {
        "intent": _build_chat_model(s.extract_intent_model),
        "customer": _build_chat_model(s.extract_customer_model),
        "items": _build_chat_model(s.extract_items_model),
        "resolver_fallback": _build_chat_model(s.extract_resolver_fallback_model),
    }


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _fail(
    stage: str,
    reason: str,
    state: _State,
    *,
    not_order_intent: str | None = None,
) -> dict[str, Any]:
    """Populate an ExtractionFailure and short-circuit to END.

    Returns a partial state update (LangGraph node convention).

    `not_order_intent` is set when classify_intent rejects the email as a
    valid non-order intent (inquiry/complaint/other). Drives the UI's
    informational-vs-error split.
    """
    snapshot = {k: _jsonable(v) for k, v in state.items() if k not in {"db", "model"}}
    failure = ExtractionFailure(
        stage=stage,  # type: ignore[arg-type]
        reason=reason,
        not_order_intent=not_order_intent,  # type: ignore[arg-type]
        partial_state=snapshot,
    )
    return {"failure": failure, "result": failure}


def _jsonable(v: Any) -> Any:
    """Best-effort JSON-safe coercion for failure snapshots."""
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return str(v)


def classify_intent_node(state: _State) -> dict[str, Any]:
    """Decide if the email is an order request at all."""
    email = state["email"]
    structured = state["models"]["intent"].with_structured_output(IntentClassification)
    prompt = (
        "Classify this email's intent.\n\n"
        f"Subject: {email.subject}\n"
        f"From: {email.from_address}\n"
        f"Body:\n{email.body_text}\n\n"
        "Fields to fill:\n"
        "- intent: one of order_request | inquiry | complaint | other\n"
        "- reasoning: one short sentence\n"
        "- evidence: 1-2 verbatim quotes from the body that justify your choice (raw text, no surrounding quotation marks)\n"
        "- confidence: 0.0-1.0 self-rated"
    )
    result: IntentClassification = structured.invoke(prompt)  # type: ignore[assignment]
    update: dict[str, Any] = {"intent": result}
    if result.intent != "order_request":
        return {
            **update,
            **_fail(
                "classify_intent",
                f"Email classified as {result.intent!r}, not an order.",
                {**state, **update},  # type: ignore[misc]
                not_order_intent=result.intent,
            ),
        }
    return update


def extract_customer_reference_node(state: _State) -> dict[str, Any]:
    """Pull the customer-identifier string out of the email body."""
    email = state["email"]
    structured = state["models"]["customer"].with_structured_output(CustomerReference)
    prompt = (
        "Find the customer or company this email is about.\n\n"
        f"Subject: {email.subject}\n"
        f"From: {email.from_address}\n"
        f"Body:\n{email.body_text}\n\n"
        "Fields to fill:\n"
        "- mentioned_name: verbatim — do NOT correct spelling or expand abbreviations\n"
        "- requester_is_customer: true unless the sender is a broker or third party\n"
        "- evidence: 1-2 verbatim quotes from the body containing the company name (raw text, no surrounding quotation marks)\n"
        "- confidence: 0.0-1.0 self-rated"
    )
    result: CustomerReference = structured.invoke(prompt)  # type: ignore[assignment]
    if not result.mentioned_name.strip():
        return _fail(
            "extract_customer",
            "Could not identify a customer name in the email body.",
            {**state, "customer_ref": result},  # type: ignore[misc]
        )
    return {"customer_ref": result}


def match_customer_node(state: _State) -> dict[str, Any]:
    """Fuzzy-match the mentioned name against the customers table."""
    ref = state["customer_ref"]
    match = find_customer_match(state["db"], ref.mentioned_name)
    if match.customer_id is None:
        return _fail(
            "match_customer",
            (
                f"No customer matches {ref.mentioned_name!r} (best score "
                f"{match.score:.1f}, min {60.0}). "
                f"Candidates inspected: {match.candidates_considered}"
            ),
            {**state, "customer_match": match},  # type: ignore[misc]
        )
    out: dict[str, Any] = {"customer_match": match}
    if match.ambiguous:
        out["notes"] = list(state.get("notes", [])) + [
            (
                f"Ambiguous customer match for {ref.mentioned_name!r}: "
                f"picked {match.customer_name!r} but "
                f"{match.candidates_considered[1]!r} was a close second."
            )
        ]
    return out


class _LineItemsEnvelope(BaseModel):
    """Wrapper so `.with_structured_output` returns a list cleanly."""

    model_config = ConfigDict(frozen=True)
    items: list[RequestedLineItem] = Field(default_factory=list)


def extract_line_items_node(state: _State) -> dict[str, Any]:
    """Ask the LLM to pull structured line items from the email body."""
    email = state["email"]
    structured = state["models"]["items"].with_structured_output(_LineItemsEnvelope)
    prompt = (
        "Extract every line item the email requests for purchase.\n\n"
        f"Body:\n{email.body_text}\n\n"
        "For each item, fill these fields:\n"
        "- description: the noun phrase ONLY. DO NOT include the quantity.\n"
        "    correct:   {description: \"hives\",         quantity: 40}\n"
        "    incorrect: {description: \"40 hives\",      quantity: 40}\n"
        "    incorrect: {description: \"a dozen queens\", quantity: 12}\n"
        "- quantity: integer >= 1; infer 'a few' = 3, 'dozen' = 12, etc.\n"
        "- mentioned_sku: SKU code ONLY if literally cited in the email\n"
        "  (e.g. POL_ALMOND_PREMIUM). Otherwise null.\n"
        "- mentioned_unit_price_usd: only if the email states a price for\n"
        "  this specific item. Otherwise null.\n"
        "- evidence: 1-2 verbatim quotes from the body justifying THIS item\n"
        "    (raw text, no surrounding quotation marks).\n"
        "- confidence: 0.0-1.0 self-rated.\n\n"
        "Return an empty items list if no specific items are requested."
    )
    envelope: _LineItemsEnvelope = structured.invoke(prompt)  # type: ignore[assignment]
    if not envelope.items:
        return _fail(
            "extract_line_items",
            "No line items could be extracted from the email body.",
            {**state, "requested_items": []},  # type: ignore[misc]
        )
    return {"requested_items": list(envelope.items)}


def resolve_skus_node(state: _State) -> dict[str, Any]:
    """Resolve every RequestedLineItem to a real Product or Service SKU.

    Pipeline per item:
        1. Deterministic exact + fuzzy match (``resolve_line_item``).
        2. If miss: LLM fallback (``resolve_via_llm``). Accepted only when
           the LLM's self-confidence clears LLM_CONFIDENCE_FLOOR.
        3. Still no match: drop the item and add a plain-language note to
           extraction_concerns so the salesperson sees what was skipped.
    """
    db = state["db"]
    catalog = _load_catalog(db)
    resolved: list[ResolvedLineItem] = []
    unresolved: list[str] = []
    for req in state["requested_items"]:
        r = resolve_line_item(db, req, catalog=catalog)
        if r is None:
            # LLM fallback — last chance before declaring this item unresolved.
            r = resolve_via_llm(req, catalog, state["models"]["resolver_fallback"])
        if r is None:
            unresolved.append(req.description)
            continue
        resolved.append(r)

    if not resolved:
        return _fail(
            "resolve_skus",
            (
                f"Could not match any of the requested items to the "
                f"catalog: {unresolved}."
            ),
            {**state, "resolved_items": []},  # type: ignore[misc]
        )

    notes = list(state.get("notes", []))
    if unresolved:
        notes.append(
            f"Dropped {len(unresolved)} unresolved item(s) — please verify: "
            f"{', '.join(unresolved)}."
        )
    return {"resolved_items": resolved, "notes": notes}


def assemble_draft_node(state: _State) -> dict[str, Any]:
    """Compute totals + aggregate per-step confidences, build the final OrderDraft."""
    items = state["resolved_items"]
    total = sum(
        (li.unit_price_usd * li.quantity for li in items),
        start=Decimal("0"),
    )
    if total <= 0:
        return _fail(
            "compute_total",
            f"Computed non-positive total {total}.",
            state,
        )
    # Aggregate overall confidence: min across intent + customer + line items.
    # Any weak step drags the overall down — conservative by design so the
    # backend can flag low-confidence drafts for human review.
    intent_conf = state["intent"].confidence
    customer_conf = state["customer_match"].confidence
    item_confs = [li.confidence for li in items]
    overall_confidence = min([intent_conf, customer_conf] + item_confs)

    draft = OrderDraft(
        customer=state["customer_match"],
        customer_evidence=list(state["customer_ref"].evidence),
        line_items=items,
        total_usd=total,
        overall_confidence=overall_confidence,
        extraction_concerns=list(state.get("notes", [])),
        source_email_external_id=state["email"].external_id,
    )
    return {"result": draft}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _has_failed(state: _State) -> Literal["fail", "continue"]:
    return "fail" if state.get("failure") is not None else "continue"


def _build_graph() -> Any:
    workflow: StateGraph = StateGraph(_State)
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("extract_customer", extract_customer_reference_node)
    workflow.add_node("match_customer", match_customer_node)
    workflow.add_node("extract_line_items", extract_line_items_node)
    workflow.add_node("resolve_skus", resolve_skus_node)
    workflow.add_node("assemble_draft", assemble_draft_node)

    workflow.set_entry_point("classify_intent")
    workflow.add_conditional_edges(
        "classify_intent",
        _has_failed,
        {"fail": END, "continue": "extract_customer"},
    )
    workflow.add_conditional_edges(
        "extract_customer",
        _has_failed,
        {"fail": END, "continue": "match_customer"},
    )
    workflow.add_conditional_edges(
        "match_customer",
        _has_failed,
        {"fail": END, "continue": "extract_line_items"},
    )
    workflow.add_conditional_edges(
        "extract_line_items",
        _has_failed,
        {"fail": END, "continue": "resolve_skus"},
    )
    workflow.add_conditional_edges(
        "resolve_skus",
        _has_failed,
        {"fail": END, "continue": "assemble_draft"},
    )
    workflow.add_edge("assemble_draft", END)
    return workflow.compile()


# Module-level singleton so repeated calls reuse the compiled graph.
_GRAPH: Any = None


def _get_graph() -> Any:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def run_order_extraction(email: EmailInput, db: Session) -> ExtractionResult:
    """Run the full extraction pipeline against an email. Returns OrderDraft or ExtractionFailure.

    Side effects:
        * DB SELECTs via the provided session.
        * One LLM call per active node (~3–5 calls per email).
    Raises:
        RuntimeError if no LLM API key is configured.
    """
    graph = _get_graph()
    initial: _State = {
        "email": email,
        "db": db,
        "models": _build_stage_models(),
        "notes": [],
    }
    final_state: _State = graph.invoke(initial)  # type: ignore[assignment]
    result = final_state.get("result")
    if result is None:
        # Defensive: should never happen because every node either populates
        # result or failure, but fail-loud if it does.
        raise RuntimeError(
            f"Extraction graph returned no result. Final state keys: "
            f"{sorted(final_state.keys())}"
        )
    return result
