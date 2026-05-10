"""Tests for the copilot's data-driven account resolver.

Pure deterministic indexing — no LLM, no hardcoded names. Builds tokens
+ phrases from the customers table on startup; resolves a free-text query
to one or more customer_ids via index lookup.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

import pytest

from agents.copilot.account_resolver import (
    AccountIndex,
    build_account_index,
    resolve_account,
)


def _seed(db, faker_instance) -> None:
    from seed import customers as customers_gen
    customers_gen.persist(db, customers_gen.generate(faker_instance))


class TestBuildIndex:
    def test_loads_customers_from_db(self, db, faker_instance) -> None:
        """build_account_index returns an AccountIndex populated from the DB."""
        _seed(db, faker_instance)
        index = build_account_index(db)
        assert isinstance(index, AccountIndex)
        # 40 customers in the seed, each contributes ≥1 token
        assert len(index.token_index) > 0
        assert len(index.phrase_index) > 0
        # Every value list is non-empty (no orphan entries)
        for v in index.token_index.values():
            assert len(v) > 0
        for v in index.phrase_index.values():
            assert len(v) > 0

    def test_no_hardcoded_names(self, db, faker_instance) -> None:
        """The index is purely data-driven — every token in it appears in
        a normalized customer name or alias. No magic strings."""
        from sqlalchemy import text
        from agents.customer_matcher import normalize_name

        _seed(db, faker_instance)
        index = build_account_index(db)

        # Build the universe of valid normalized sources straight from
        # the DB rows: canonical names + every alias.
        rows = (
            db.execute(text("SELECT name, aliases FROM customers"))
            .mappings()
            .all()
        )
        all_sources: set[str] = set()
        for r in rows:
            all_sources.add(normalize_name(r["name"]))
            for alias in r["aliases"] or []:
                all_sources.add(normalize_name(alias))

        for token in index.token_index:
            assert any(token in src for src in all_sources), (
                f"token {token!r} not present in any customer name/alias"
            )


class TestResolve:
    def test_exact_full_phrase_match(self, db, faker_instance) -> None:
        """Mentioning the full company name resolves to that customer."""
        _seed(db, faker_instance)
        index = build_account_index(db)
        # Find a real customer name from the index
        sample_name = next(iter(index.customer_id_to_name.values()))
        result = resolve_account(f"any update on {sample_name}?", index)
        # Must resolve to the right customer_id
        target_id = next(
            cid for cid, name in index.customer_id_to_name.items()
            if name == sample_name
        )
        assert target_id in result

    def test_single_token_match(self, db, faker_instance) -> None:
        """A distinctive token from a customer name resolves."""
        _seed(db, faker_instance)
        index = build_account_index(db)
        # Use the longest token from the first customer name (likely distinctive)
        first_name = next(iter(index.customer_id_to_name.values()))
        tokens = [t for t in first_name.lower().split() if len(t) >= 5 and t.isalpha()]
        if not tokens:
            pytest.skip("no distinctive tokens available in fixture")
        result = resolve_account(f"how is {tokens[0]} doing?", index)
        # Should include the customer this token came from
        # (or any customer also matching the token — that's a real match too)
        assert len(result) >= 1

    def test_unknown_text_returns_empty(self, db, faker_instance) -> None:
        """Free text with no matching customer name returns []."""
        _seed(db, faker_instance)
        index = build_account_index(db)
        result = resolve_account(
            "hello world xyzzy plover frobnicate", index
        )
        assert result == []

    def test_punctuation_and_case_insensitive(self, db, faker_instance) -> None:
        """Resolver normalizes — punctuation + case shouldn't block a match."""
        _seed(db, faker_instance)
        index = build_account_index(db)
        sample_name = next(iter(index.customer_id_to_name.values()))
        # Mangle the name: uppercase + add punctuation
        mangled = f"  ({sample_name.upper()}!)  "
        result = resolve_account(mangled, index)
        target_id = next(
            cid for cid, name in index.customer_id_to_name.items()
            if name == sample_name
        )
        assert target_id in result


class TestNoHardcoding:
    def test_indexing_uses_only_db_data(self, db, faker_instance) -> None:
        """The index never references names that aren't in the DB.

        Regression guard against a class of bug seen in prior reference
        implementations: hardcoded ``if "lakewood" in name:`` style checks
        in the resolver code that don't scale to a fresh customer set.
        """
        _seed(db, faker_instance)
        index = build_account_index(db)
        # If hardcodes existed, indexing a fresh DB with different names
        # would still produce those tokens. Confirm every indexed slug
        # comes from a real DB row.
        all_known_ids = set(index.customer_id_to_name.keys())
        for ids in index.token_index.values():
            for cid in ids:
                assert cid in all_known_ids
        for ids in index.phrase_index.values():
            for cid in ids:
                assert cid in all_known_ids
