"""E2E tests for the fuzzy customer matcher.

Pure DB work — no LLM involved. Confirms normalization rules, ambiguity
detection, and the score-floor behavior.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from agents.customer_matcher import (
    AMBIGUITY_MARGIN,
    SCORE_MIN,
    find_customer_match,
    normalize_name,
)


class TestNormalization:
    def test_strips_diacritics(self) -> None:
        assert normalize_name("Renée Boucher") == "renee boucher"

    def test_ampersand_expands(self) -> None:
        assert normalize_name("Martinez & Sons") == "martinez and sons"

    def test_apostrophes_dropped(self) -> None:
        assert normalize_name("O'Reilly Family") == "oreilly family"

    def test_corporate_suffix_stripped(self) -> None:
        assert normalize_name("Meridian Apiaries LLC") == "meridian apiaries"
        assert normalize_name("Cascade Orchards Inc") == "cascade orchards"

    def test_whitespace_collapsed(self) -> None:
        assert normalize_name("  foo   bar  ") == "foo bar"


class TestFindMatch:
    def _seed(self, db, faker_instance) -> None:
        from seed import customers as customers_gen
        customers_gen.persist(db, customers_gen.generate(faker_instance))

    def test_exact_match_scores_100(self, db, faker_instance) -> None:
        self._seed(db, faker_instance)
        result = find_customer_match(db, "O'Reilly Family Orchards")
        assert result.customer_id is not None
        assert result.customer_name == "O'Reilly Family Orchards"
        assert result.score >= 95.0

    def test_alias_match_resolves_to_canonical(self, db, faker_instance) -> None:
        self._seed(db, faker_instance)
        # Alias from the edge-case list in seed/faker_providers.py
        result = find_customer_match(db, "OReilly")
        assert result.customer_id is not None
        assert "O'Reilly" in (result.customer_name or "")

    def test_unknown_name_returns_no_match(self, db, faker_instance) -> None:
        self._seed(db, faker_instance)
        result = find_customer_match(db, "Completely Unrelated Business")
        assert result.customer_id is None
        assert result.score < SCORE_MIN

    def test_misspelled_but_close_still_matches(self, db, faker_instance) -> None:
        self._seed(db, faker_instance)
        result = find_customer_match(db, "O Reily Family Orchrds")
        assert result.customer_id is not None
        assert result.score >= SCORE_MIN

    def test_candidates_considered_populated(self, db, faker_instance) -> None:
        self._seed(db, faker_instance)
        result = find_customer_match(db, "Orchard")
        assert len(result.candidates_considered) > 0
        assert len(result.candidates_considered) <= 5

    def test_ambiguity_margin_constant_sane(self) -> None:
        # Guard against someone tuning these to zero accidentally.
        assert AMBIGUITY_MARGIN > 0
        assert SCORE_MIN > 0
