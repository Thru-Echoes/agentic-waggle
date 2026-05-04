"""E2E tests for red-flag rules."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import red_flags as red_flags_gen


class TestGenerate:
    def test_exactly_ten_rules(self) -> None:
        assert len(red_flags_gen.generate()) == 10

    def test_rule_names_unique(self) -> None:
        names = [r.rule_name for r in red_flags_gen.generate()]
        assert len(names) == len(set(names))

    def test_all_scopes_exercised(self) -> None:
        scopes = {r.scope for r in red_flags_gen.generate()}
        assert scopes == {"site", "customer", "user", "order"}

    def test_all_severities_are_valid(self) -> None:
        for r in red_flags_gen.generate():
            assert r.severity in {"info", "warn", "block"}


class TestPersist:
    def test_inserts_all(self, db) -> None:
        red_flags_gen.persist(db, red_flags_gen.generate())
        assert db.execute(text("SELECT COUNT(*) FROM red_flag_rules")).scalar() == 10

    def test_duplicate_rule_name_raises(self, db) -> None:
        red_flags_gen.persist(db, red_flags_gen.generate())
        with pytest.raises(IntegrityError):
            red_flags_gen.persist(db, red_flags_gen.generate())
