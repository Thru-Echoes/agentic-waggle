"""Visitor-scoped conversations and persisted citations.

conversations.visitor_id — opaque id from the signed browser cookie that
    created the conversation. The rail and the landing page show only the
    current visitor's conversations; rows with NULL (created before this
    migration, or through the JSON API) stay reachable by URL but are not
    listed. One demo login is shared by every reviewer, so without this a
    visitor landed inside whoever chatted last.

chat_turns.citations — the source rows that grounded an assistant answer
    ({kind, id, label, excerpt}); previously computed per response and
    discarded, so the JSON API always returned an empty list.

Revision ID: 005_visitor_citations
Revises: 004_semantic_matcher
Create Date: 2026-11-02

"""
# pyright: reportMissingImports=false
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "005_visitor_citations"
down_revision: Union[str, None] = "004_semantic_matcher"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("visitor_id", sa.Text(), nullable=True))
    op.create_index("ix_conversations_visitor_id", "conversations", ["visitor_id"])
    op.add_column(
        "chat_turns",
        sa.Column("citations", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("chat_turns", "citations")
    op.drop_index("ix_conversations_visitor_id", table_name="conversations")
    op.drop_column("conversations", "visitor_id")
