"""Add tags column to customer_reports for faceted retrieval.

`tags` is a JSONB list of short keyword phrases (snake_case) that an
LLM-driven fixture generator produces alongside each narrative. They
are surfaced through the chunk metadata so the Sales Copilot MCP tool
can do hybrid filter+semantic queries (e.g. retrieve `regulatory_alert`
chunks then rank by cosine to the question).

Revision ID: 002_add_customer_report_tags
Revises: 001_initial_schema
Create Date: 2026-05-04

"""
# pyright: reportMissingImports=false
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002_add_customer_report_tags"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customer_reports",
        sa.Column(
            "tags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("customer_reports", "tags")
