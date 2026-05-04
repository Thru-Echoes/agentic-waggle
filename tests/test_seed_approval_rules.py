"""E2E tests for approval rules."""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from seed import approval_rules as approval_rules_gen


class TestGenerate:
    def test_exactly_ten_rules(self) -> None:
        assert len(approval_rules_gen.generate()) == 10

    def test_rule_names_unique(self) -> None:
        names = [r.rule_name for r in approval_rules_gen.generate()]
        assert len(names) == len(set(names))

    def test_roles_are_valid(self) -> None:
        for r in approval_rules_gen.generate():
            assert r.requires_role in {"apiarist", "ops_manager", "admin", "none", "reject"}

    def test_default_rule_present(self) -> None:
        names = {r.rule_name for r in approval_rules_gen.generate()}
        assert "default_auto_approve" in names


class TestPersist:
    def test_inserts_all(self, db) -> None:
        approval_rules_gen.persist(db, approval_rules_gen.generate())
        assert db.execute(text("SELECT COUNT(*) FROM approval_rules")).scalar() == 10

    def test_duplicate_rule_name_raises(self, db) -> None:
        approval_rules_gen.persist(db, approval_rules_gen.generate())
        with pytest.raises(IntegrityError):
            approval_rules_gen.persist(db, approval_rules_gen.generate())
