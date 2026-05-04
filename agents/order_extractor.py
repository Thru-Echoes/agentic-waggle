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
from agents.sku_resolver import _load_catalog, resolve_line_item

log = logging.getLogger(__name__)


class _State(TypedDict):
    """Accumulated graph state. Required fields are set at entry; NotRequired
    fields are populated progressively by nodes. `result` is set exactly once
    at the terminal node (either assemble_draft or an early failure)."""

    email: EmailInput
    db: Session  # stashed here so nodes can query without dependency injection
    model: BaseChatModel
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


def _build_chat_model() -> BaseChatModel:
    """Return a configured LangChain chat model chosen by `Settings.llm_provider`.

    Reuses `providers.llm.resolve_provider()` so the "which provider?"
    decision is single-sourced. Raises `LLMConfigurationError` if no key is
    set, matching `providers.llm`'s fail-loud contract.

    Why kwargs built dynamically: pyright stubs for langchain-openai and
    langchain-anthropic lag behind the 1.x runtime API (which prefers
    `model=`). Passing a **kwargs dict sidesteps that stub check without
    silencing genuine type errors.
    """
    from app.config import get_settings
    from providers.llm import resolve_provider

    settings = get_settings()
    provider = resolve_provider()  # raises LLMConfigurationError on no keys

    if provider == "openai":
        openai_kwargs: dict[str, Any] = {
            "model": settings.openai_model,
            "temperature": 0.0,
            "timeout": 60,
            "max_retries": 2,
        }
        return ChatOpenAI(**openai_kwargs)
    anthropic_kwargs: dict[str, Any] = {
        "model": settings.anthropic_model,
        "temperature": 0.0,
        "timeout": 60,
        "max_retries": 2,
    }
    return ChatAnthropic(**anthropic_kwargs)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _fail(stage: str, reason: str, state: _State) -> dict[str, Any]:
    """Populate an ExtractionFailure and short-circuit to END.

    Returns a partial state update (LangGraph node convention).
    """
    snapshot = {k: _jsonable(v) for k, v in state.items() if k not in {"db", "model"}}
    failure = ExtractionFailure(
        stage=stage,  # type: ignore[arg-type]
        reason=reason,
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
    structured = state["model"].with_structured_output(IntentClassification)
    prompt = (
        "Classify the intent of this email from a beekeeping CRM customer.\n\n"
        f"From: {email.from_address}\n"
        f"Subject: {email.subject}\n\n"
        f"Body:\n{email.body_text}\n\n"
        "Return one of: order_request | inquiry | complaint | other. "
        "Include a short reasoning."
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
            ),
        }
    return update


def extract_customer_reference_node(state: _State) -> dict[str, Any]:
    """Pull the customer-identifier string out of the email body."""
    email = state["email"]
    structured = state["model"].with_structured_output(CustomerReference)
    prompt = (
        "Find the customer / company name this email is about.\n\n"
        f"From: {email.from_address}\n"
        f"Subject: {email.subject}\n\n"
        f"Body:\n{email.body_text}\n\n"
        "Extract `mentioned_name` verbatim — do not correct spelling or "
        "expand abbreviations. Set `requester_is_customer=True` unless the "
        "email is clearly from a broker or third party asking on behalf of "
        "a different customer."
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
    structured = state["model"].with_structured_output(_LineItemsEnvelope)
    prompt = (
        "Extract every line item requested in this email. Return an empty "
        "items list if nothing specific is asked for.\n\n"
        f"Body:\n{email.body_text}\n\n"
        "For each item: `description` (verbatim noun phrase), `quantity` "
        "(integer ≥ 1 — infer from wording like 'a few' = 3, 'dozen' = 12), "
        "`mentioned_sku` (only if a specific SKU code is cited — e.g. "
        "POL_ALMOND_PREMIUM), `mentioned_unit_price_usd` (only if the email "
        "cites a price)."
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
    """Resolve every RequestedLineItem to a real Product or Service SKU."""
    db = state["db"]
    catalog = _load_catalog(db)
    resolved: list[ResolvedLineItem] = []
    unresolved: list[str] = []
    for req in state["requested_items"]:
        r = resolve_line_item(db, req, catalog=catalog)
        if r is None:
            unresolved.append(req.description)
            continue
        resolved.append(r)

    if not resolved:
        return _fail(
            "resolve_skus",
            (
                f"Could not resolve any of the requested items: {unresolved}. "
                f"Catalog has {len(catalog)} SKUs."
            ),
            {**state, "resolved_items": []},  # type: ignore[misc]
        )

    notes = list(state.get("notes", []))
    if unresolved:
        notes.append(
            f"Dropped {len(unresolved)} unresolved line item(s): {unresolved}."
        )
    return {"resolved_items": resolved, "notes": notes}


def assemble_draft_node(state: _State) -> dict[str, Any]:
    """Compute totals, build the final OrderDraft."""
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
    draft = OrderDraft(
        customer=state["customer_match"],
        line_items=items,
        total_usd=total,
        source_email_external_id=state["email"].external_id,
        extraction_notes=list(state.get("notes", [])),
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
        "model": _build_chat_model(),
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
