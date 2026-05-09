"""Resolve a free-text line-item description to a concrete Product or Service SKU.

Three strategies, tried in order:
    1. **Exact SKU** — the email literally cites ``POL_ALMOND_PREMIUM`` or
       similar. Case-insensitive equality match against ``products.sku``
       and ``services.sku``.
    2. **Fuzzy name** — rapidfuzz over ``product.name`` + ``service.name``.
       Score above ``FUZZY_SCORE_MIN`` wins.
    3. **LLM fallback** — when (1) and (2) miss, the LLM is given the
       requested description + the full catalog and asked to pick the
       best match with self-rated evidence + confidence. The pick is
       only accepted if ``confidence >= LLM_CONFIDENCE_FLOOR``;
       otherwise we fail loud (return None) so the agent surfaces an
       unresolved item to the salesperson rather than silently guessing.

All three strategies return the same ``ResolvedLineItem`` shape.

Side effects: SELECTs ``products`` + ``services``; strategy (3) makes one
LLM call.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from decimal import Decimal
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import Field
from rapidfuzz import fuzz, process
from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.schemas import Grounded, RequestedLineItem, ResolvedLineItem

FUZZY_SCORE_MIN = 72.0
# LLM-fallback rejects below this confidence — better to fail loud than
# to silently pick a wrong SKU.
LLM_CONFIDENCE_FLOOR = 0.6


def _load_catalog(db: Session) -> list[dict[str, Any]]:
    """Return every product + service as dicts with kind, id, sku, name, price."""
    rows: list[dict[str, Any]] = []
    for row in db.execute(
        text("SELECT id, sku, name, price_usd FROM products")
    ).mappings():
        rows.append(
            {
                "kind": "product",
                "id": row["id"],
                "sku": row["sku"],
                "name": row["name"],
                "price": Decimal(row["price_usd"]),
            }
        )
    for row in db.execute(
        text("SELECT id, sku, name, default_rate_usd FROM services")
    ).mappings():
        price = row["default_rate_usd"]
        rows.append(
            {
                "kind": "service",
                "id": row["id"],
                "sku": row["sku"],
                "name": row["name"],
                "price": Decimal(price) if price is not None else Decimal("0"),
            }
        )
    return rows


def _fuzzy_score_to_confidence(score: float) -> float:
    """Map rapidfuzz score (0-100) to confidence (0.0-1.0) for fuzzy_name matches.

    Linear above 50 — at score=100, confidence=1.0; at score=50, confidence=0.0.
    Floored at 0. Pure function.
    """
    return max(0.0, min(1.0, (score - 50.0) / 50.0))


def _build_resolved(
    entry: dict[str, Any],
    req: RequestedLineItem,
    method: str,
    *,
    score: float | None = None,
) -> ResolvedLineItem:
    """Construct a ResolvedLineItem with method-appropriate evidence + confidence.

    Evidence list combines (a) the source quote from the email — propagated
    from ``req.evidence`` so the UI can answer "where did this item come from?"
    — with (b) for fuzzy matches, the catalog name that matched.

    - exact_sku:    evidence = source-only;   confidence = 1.0
    - fuzzy_name:   evidence = source + catalog-name;  confidence from score
    - llm_guess:    constructed in resolve_via_llm (which combines source
                    + LLM-cited catalog quotes directly).
    """
    price = req.mentioned_unit_price_usd or entry["price"]
    source_evidence = list(req.evidence)
    if method == "exact_sku":
        evidence = source_evidence
        confidence = 1.0
    elif method == "fuzzy_name":
        evidence = source_evidence + [str(entry["name"])]
        s = score if score is not None else 75.0
        confidence = _fuzzy_score_to_confidence(s)
    else:
        # llm_guess builds ResolvedLineItem directly in resolve_via_llm.
        evidence = source_evidence
        confidence = 0.0
    return ResolvedLineItem(
        sku=entry["sku"],
        product_id=entry["id"] if entry["kind"] == "product" else None,
        service_id=entry["id"] if entry["kind"] == "service" else None,
        quantity=req.quantity,
        unit_price_usd=Decimal(price),
        match_method=method,  # type: ignore[arg-type]
        source_description=req.description,
        evidence=evidence,
        confidence=confidence,
    )


def resolve_line_item(
    db: Session,
    req: RequestedLineItem,
    catalog: list[dict[str, Any]] | None = None,
) -> ResolvedLineItem | None:
    """Attempt exact-then-fuzzy resolution. Returns None if no match ≥ threshold.

    Callers that want an LLM fallback should chain to `resolve_via_llm`
    after None is returned here.

    Side effects: loads the catalog on first call if not supplied.
    """
    if catalog is None:
        catalog = _load_catalog(db)

    # 1. Exact SKU
    if req.mentioned_sku:
        target = req.mentioned_sku.strip().upper()
        for entry in catalog:
            if entry["sku"].upper() == target:
                return _build_resolved(entry, req, "exact_sku")

    # 2. Fuzzy name
    names = [e["name"] for e in catalog]
    best = process.extractOne(
        req.description,
        names,
        scorer=fuzz.WRatio,
    )
    if best is not None:
        _, score, idx = best
        if score >= FUZZY_SCORE_MIN:
            return _build_resolved(catalog[idx], req, "fuzzy_name", score=score)

    return None


# ---------------------------------------------------------------------------
# Strategy 3: LLM fallback
# ---------------------------------------------------------------------------


class _LLMResolution(Grounded):
    """Schema the LLM fills when picking a SKU for an unresolved description.

    Grounded → evidence + confidence are required. ``chosen_sku`` is the
    SKU from the catalog (or null when the LLM judges no catalog entry
    is a reasonable match). The runtime requires ``confidence >=
    LLM_CONFIDENCE_FLOOR`` for the resolution to be accepted.
    """

    chosen_sku: str | None = Field(
        default=None,
        description=(
            "SKU code from the catalog. Set to null when no catalog entry "
            "is a reasonable match for the requested description."
        ),
    )
    reasoning: str = Field(
        ..., max_length=200, description="One short sentence."
    )


def resolve_via_llm(
    req: RequestedLineItem,
    catalog: list[dict[str, Any]],
    model: BaseChatModel,
    *,
    confidence_floor: float = LLM_CONFIDENCE_FLOOR,
) -> ResolvedLineItem | None:
    """Last-resort SKU resolver. Returns None on no-match OR low-confidence.

    The LLM sees the requested description plus the entire catalog (typically
    <200 SKUs) and returns a chosen SKU + verbatim evidence (catalog row text
    that triggered the choice) + self-rated confidence.

    No silent guesses: a confidence below the floor is treated as "no
    resolution" and the caller surfaces it to the user via
    ``extraction_concerns``.

    Side effects: one LLM call.
    """
    catalog_lines = "\n".join(
        f"- {entry['sku']}  [{entry['kind']}]  {entry['name']}"
        for entry in catalog
    )
    structured = model.with_structured_output(_LLMResolution)
    prompt = (
        "Pick the catalog SKU that best matches this requested item.\n\n"
        f"Requested:\n"
        f"  description: {req.description}\n"
        f"  quantity:    {req.quantity}\n"
        f"  cited SKU:   {req.mentioned_sku or '(none)'}\n\n"
        f"Catalog ({len(catalog)} entries):\n{catalog_lines}\n\n"
        "Fields to fill:\n"
        "- chosen_sku: the SKU code from the catalog above. Set to null\n"
        "  when no catalog entry is a reasonable match.\n"
        "- reasoning: one short sentence.\n"
        "- evidence: the catalog row text(s) you matched against, verbatim.\n"
        "- confidence: 0.0-1.0 self-rated. Be honest — if you're guessing,\n"
        "  confidence should be low so the system can flag this for review."
    )
    result: _LLMResolution = structured.invoke(prompt)  # type: ignore[assignment]

    if result.chosen_sku is None or result.confidence < confidence_floor:
        return None

    # Look up the LLM's chosen SKU in the catalog. If the LLM hallucinated
    # a SKU not actually in the catalog, fail loud (return None).
    target = result.chosen_sku.strip().upper()
    chosen = next(
        (e for e in catalog if str(e["sku"]).upper() == target),
        None,
    )
    if chosen is None:
        return None

    price = req.mentioned_unit_price_usd or chosen["price"]
    return ResolvedLineItem(
        sku=str(chosen["sku"]),
        product_id=chosen["id"] if chosen["kind"] == "product" else None,
        service_id=chosen["id"] if chosen["kind"] == "service" else None,
        quantity=req.quantity,
        unit_price_usd=Decimal(price),
        match_method="llm_guess",
        source_description=req.description,
        evidence=list(req.evidence) + list(result.evidence),
        confidence=result.confidence,
    )
