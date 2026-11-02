"""Migration 005 contract against tests/schema.sql: visitor scoping + citations."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def _columns(db: Session, table: str) -> dict[str, str]:
    rows = db.execute(
        text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = :t"),
        {"t": table},
    ).fetchall()
    return {r.column_name: r.data_type for r in rows}


def test_conversations_have_a_nullable_visitor_id_with_an_index(db: Session) -> None:
    cols = _columns(db, "conversations")
    assert cols["visitor_id"] == "text"
    idx = db.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'conversations'")
    ).scalars().all()
    assert "ix_conversations_visitor_id" in idx


def test_chat_turns_have_citations_jsonb_defaulting_to_empty(db: Session) -> None:
    cols = _columns(db, "chat_turns")
    assert cols["citations"] == "jsonb"
    db.execute(text("INSERT INTO conversations (id) VALUES (gen_random_uuid())"))
    cid = db.execute(text("SELECT id FROM conversations LIMIT 1")).scalar_one()
    db.execute(
        text("INSERT INTO chat_turns (conversation_id, role, text) VALUES (:c, 'user', 'hi')"),
        {"c": str(cid)},
    )
    assert db.execute(text("SELECT citations FROM chat_turns LIMIT 1")).scalar_one() == []


def test_every_alembic_revision_id_fits_the_version_table() -> None:
    """alembic_version.version_num is VARCHAR(32); a longer id makes
    ``alembic upgrade head`` fail at the version bookkeeping step, which
    on the live deploy means a boot loop."""
    import re
    from pathlib import Path

    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    too_long: list[str] = []
    for path in sorted(versions.glob("*.py")):
        m = re.search(r'^revision: str = "([^"]+)"', path.read_text(), re.M)
        assert m, f"{path.name} has no revision id"
        if len(m.group(1)) > 32:
            too_long.append(f"{path.name}: {m.group(1)} ({len(m.group(1))} chars)")
    assert too_long == [], too_long
