"""Fuzzy customer-name matcher.

Shared by the order-extraction agent and the Sales Copilot MCP server.
Uses `rapidfuzz` (C-backed) for fast Levenshtein-style scoring, with
domain-specific normalization up front to make apostrophes, ampersands,
`& Sons`, and the like not blow up the match score.

All functions are pure + deterministic — same input, same output,
regardless of call order. Side effects: `find_customer_match` executes
a `SELECT id, name, erp_customer_no, aliases FROM customers` on the
provided session.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Sequence

from rapidfuzz import fuzz, process
from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.schemas import CustomerMatch

# Anything scoring below this is considered a miss (no match).
SCORE_MIN = 60.0
# Gap between top-1 and top-2 below which the match is flagged ambiguous.
AMBIGUITY_MARGIN = 6.0


def _score_to_confidence(score: float) -> float:
    """Map a rapidfuzz 0-100 score to a confidence in [0.0, 1.0].

    Banded calibration:
        - score >= 95  → 0.95   (effectively certain)
        - score >= 85  → 0.85   (high)
        - score >= 75  → 0.70   (medium)
        - score >= SCORE_MIN (60) → 0.50  (low but acceptable)
        - else → 0.0   (no match)

    Pure function. Backend-only — feeds OrderDraft.overall_confidence
    aggregation; never rendered.
    """
    if score >= 95:
        return 0.95
    if score >= 85:
        return 0.85
    if score >= 75:
        return 0.70
    if score >= SCORE_MIN:
        return 0.50
    return 0.0


def normalize_name(name: str) -> str:
    """Canonicalize a customer name for fuzzy comparison.

    Rules (applied in order):
        1. NFD unicode-normalize, strip diacritics
        2. lowercase
        3. replace `&` → ` and `
        4. drop `.`, `,`, `'`, `"`
        5. collapse whitespace
        6. drop common trailing tokens: `inc`, `llc`, `ltd`, `co`

    Pure function; no side effects.
    """
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[.,'\"’‘]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = s.split(" ")
    while tokens and tokens[-1] in {"inc", "llc", "ltd", "co", "corp", "company"}:
        tokens.pop()
    return " ".join(tokens)


def _candidate_strings(rows: Sequence[dict]) -> list[tuple[str, dict]]:
    """Expand every customer into (normalized_name_or_alias, row) pairs.

    Each row contributes one entry per (canonical_name + alias). Downstream
    scoring picks the best of all expansions, so `"O'Reilly Family Orchards"`
    matches `"OReilly Family"` via the canonical name, and `"5 Rivers"`
    matches via the alias list, without double-counting any customer.
    """
    out: list[tuple[str, dict]] = []
    for r in rows:
        out.append((normalize_name(r["name"]), r))
        for alias in r.get("aliases") or []:
            out.append((normalize_name(alias), r))
    return out


def find_customer_match(db: Session, mentioned_name: str) -> CustomerMatch:
    """Resolve a free-text customer mention against the `customers` table.

    Returns a `CustomerMatch` with `customer_id=None` when nothing scores
    over `SCORE_MIN`; the caller decides whether to fail loud or ask the
    user for disambiguation.

    Side effects: one SELECT against the provided DB session.
    """
    rows = (
        db.execute(
            text("SELECT id, name, erp_customer_no, aliases FROM customers")
        )
        .mappings()
        .all()
    )
    rows_as_dicts = [dict(r) for r in rows]

    candidates = _candidate_strings(rows_as_dicts)
    query = normalize_name(mentioned_name)

    # rapidfuzz accepts (query, choices) where choices is an iterable of
    # the candidate strings; we pair score → row externally.
    scored = process.extract(
        query,
        [c[0] for c in candidates],
        scorer=fuzz.WRatio,
        limit=len(candidates),
    )

    # Consolidate to one best hit per customer.id
    best_per_customer: dict[str, tuple[float, dict]] = {}
    for cand_str, score, idx in scored:
        row = candidates[idx][1]
        cid = str(row["id"])
        prior = best_per_customer.get(cid)
        if prior is None or score > prior[0]:
            best_per_customer[cid] = (score, row)

    ranked = sorted(best_per_customer.values(), key=lambda t: t[0], reverse=True)
    top_names = [r[1]["name"] for r in ranked[:5]]

    if not ranked or ranked[0][0] < SCORE_MIN:
        miss_score = ranked[0][0] if ranked else 0.0
        return CustomerMatch(
            customer_id=None,
            customer_name=None,
            erp_customer_no=None,
            score=miss_score,
            confidence=_score_to_confidence(miss_score),
            ambiguous=False,
            candidates_considered=top_names,
        )

    best_score, best_row = ranked[0]
    ambiguous = (
        len(ranked) >= 2
        and (best_score - ranked[1][0]) < AMBIGUITY_MARGIN
        and ranked[1][0] >= SCORE_MIN
    )
    return CustomerMatch(
        customer_id=best_row["id"],
        customer_name=best_row["name"],
        erp_customer_no=best_row["erp_customer_no"],
        score=best_score,
        confidence=_score_to_confidence(best_score),
        ambiguous=ambiguous,
        candidates_considered=top_names,
    )
