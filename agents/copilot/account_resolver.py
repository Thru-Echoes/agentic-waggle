"""Pure deterministic account resolver for the sales copilot.

Builds an in-memory index of customer names + aliases from the DB once
(at copilot startup, refreshed when customers change). Resolves a free-
text query — a chat message, a transcribed dictation, etc. — to one or
more ``customer_id`` values via fast token / phrase lookup.

No LLM. No hardcoded customer names. Every entry in the index comes
from a row in the ``customers`` table; rebuilding from a fresh DB
yields a fresh index without code changes.

This complements ``agents/customer_matcher.py`` (rapidfuzz-based fuzzy
match used inside the order extractor): the resolver here is the fast
exact / token / phrase path used per-chat-turn. When that returns no
hits, the orchestrator can chain into ``find_customer_match`` for the
fuzzy fallback.

Side effects:
    * ``build_account_index`` — one SELECT against ``customers``.
    * ``resolve_account`` — pure / no I/O.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.customer_matcher import normalize_name


# Tokens shorter than this don't earn a slot in the index — too generic
# (e.g. "co", "of", "the") and would create false matches.
MIN_TOKEN_LEN = 4

# Generic business-name fragments that should never trigger a match.
GENERIC_TOKENS = frozenset({
    "company", "corp", "supply", "supplies", "inc", "llc", "ltd",
    "group", "services", "service", "solutions", "international",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountIndex:
    """In-memory index of customers, built once and reused per chat turn.

    Both indexes map a normalized search key → list of customer_ids that
    contain that key. Multi-customer lists let an ambiguous match
    surface every candidate; the orchestrator picks based on conversation
    context (active_account_slug, recent turns).
    """

    token_index: dict[str, list[uuid.UUID]] = field(default_factory=dict)
    phrase_index: dict[str, list[uuid.UUID]] = field(default_factory=dict)
    customer_id_to_name: dict[uuid.UUID, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------


def _add_to_index(
    index: dict[str, list[uuid.UUID]],
    key: str,
    customer_id: uuid.UUID,
) -> None:
    """Append ``customer_id`` to ``index[key]``, creating the list if absent."""
    bucket = index.setdefault(key, [])
    if customer_id not in bucket:
        bucket.append(customer_id)


def build_account_index(db: Session) -> AccountIndex:
    """Load every customer + their aliases from ``customers`` and index them.

    Side effects: one SELECT against ``customers``.
    """
    rows = (
        db.execute(text("SELECT id, name, aliases FROM customers"))
        .mappings()
        .all()
    )

    token_index: dict[str, list[uuid.UUID]] = {}
    phrase_index: dict[str, list[uuid.UUID]] = {}
    customer_id_to_name: dict[uuid.UUID, str] = {}

    for row in rows:
        cid = row["id"]
        if isinstance(cid, str):
            cid = uuid.UUID(cid)
        customer_id_to_name[cid] = row["name"]

        # Sources: the canonical name + any aliases on the row.
        sources: list[str] = [row["name"]]
        for alias in row.get("aliases") or []:
            sources.append(alias)

        for source in sources:
            normalized = normalize_name(source)
            if not normalized:
                continue
            # Phrase entry — only meaningful if multi-token and long enough
            # to be distinctive (avoids ``"of"`` style noise).
            if " " in normalized and len(normalized) >= 6:
                _add_to_index(phrase_index, normalized, cid)
            # Token entries — every safe word in the normalized source.
            for tok in normalized.split():
                if len(tok) < MIN_TOKEN_LEN:
                    continue
                if tok in GENERIC_TOKENS:
                    continue
                if not tok.isalpha():
                    continue
                _add_to_index(token_index, tok, cid)

    return AccountIndex(
        token_index=token_index,
        phrase_index=phrase_index,
        customer_id_to_name=customer_id_to_name,
    )


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _normalize_query(text_in: str) -> str:
    """Apply the same normalization used at index time, plus aggressive
    punctuation stripping for free-text chat queries."""
    s = (text_in or "").lower()
    # Strip everything that isn't a letter/digit/space — chat input has
    # quotes, parens, exclamation marks, etc.
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_account(text_in: str, index: AccountIndex) -> list[uuid.UUID]:
    """Return all ``customer_id`` values matched by ``text_in``.

    Resolution order:
        1. Phrase match (multi-token canonical name appears in the query)
        2. Token match (any safe distinctive word from a customer name)

    Empty list = no match. Caller may fall back to a fuzzy resolver.

    Pure function — same input, same output.
    """
    if not text_in:
        return []
    normalized = _normalize_query(text_in)
    if not normalized:
        return []

    found: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()

    def _add(ids: list[uuid.UUID]) -> None:
        for cid in ids:
            if cid not in seen:
                seen.add(cid)
                found.append(cid)

    # Phrase pass — prefer longer phrases (more specific).
    for phrase in sorted(index.phrase_index.keys(), key=len, reverse=True):
        if phrase in normalized:
            _add(index.phrase_index[phrase])

    # Token pass — split the query into words and look each up.
    query_tokens = set(normalized.split())
    for tok in query_tokens:
        ids = index.token_index.get(tok)
        if ids:
            _add(ids)

    return found
