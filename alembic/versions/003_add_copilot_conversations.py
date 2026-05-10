"""Add conversations + chat_turns tables for the sales copilot.

Foundation for sub-project B (the chat-style sales copilot). Two tables:

  conversations  — one row per copilot session. Tracks the salesperson
                   who owns it (nullable when the demo is anonymous), the
                   active customer context, and the model that handled
                   the most recent turn (for observability rollups).

  chat_turns     — append-only log of every turn (user / assistant /
                   system / tool). Stores the verbatim evidence used to
                   ground the assistant response, the structured
                   tool-call records, and per-turn metrics (tokens,
                   cost, latency). JSONB for the variable-shape
                   payloads so we don't need a third table.

Indexes:
  ix_conversations_updated_at         — sort the conversation list by
                                        most-recently-active.
  ix_conversations_salesperson_id     — list a salesperson's conversations.
  ix_chat_turns_conversation_id       — replay a conversation in order
                                        (composite with created_at).

Revision ID: 003_add_copilot_conversations
Revises: 002_add_customer_report_tags
Create Date: 2026-05-09

"""
# pyright: reportMissingImports=false
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "003_add_copilot_conversations"
down_revision: Union[str, None] = "002_add_customer_report_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "salesperson_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "active_account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_conversations_salesperson_id",
        "conversations",
        ["salesperson_id"],
    )
    op.create_index(
        "ix_conversations_updated_at",
        "conversations",
        ["updated_at"],
    )

    op.create_table(
        "chat_turns",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "tool_calls",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "evidence_used",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "tokens_input",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tokens_output",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(10, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="chat_turns_role_check",
        ),
    )
    op.create_index(
        "ix_chat_turns_conversation_id",
        "chat_turns",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_turns_conversation_id",
        table_name="chat_turns",
    )
    op.drop_table("chat_turns")
    op.drop_index(
        "ix_conversations_updated_at",
        table_name="conversations",
    )
    op.drop_index(
        "ix_conversations_salesperson_id",
        table_name="conversations",
    )
    op.drop_table("conversations")
