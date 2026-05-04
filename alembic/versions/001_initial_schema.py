"""Initial schema — all 17 tables + pgvector.

Mirrors `tests/schema.sql` column-for-column with the same constraint
semantics. Every FK declares its `ondelete` explicitly so downstream
behavior matches the test schema.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-04-22

"""
# pyright: reportMissingImports=false
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector  # type: ignore[import-not-found]
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector extension is required for `chunks.embedding` below. Idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # -- Reference / directory ---------------------------------------------
    op.create_table(
        "territories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Text(),
            sa.CheckConstraint(
                "role IN ('apiarist', 'ops_manager', 'admin')",
                name="users_role_check",
            ),
            nullable=False,
        ),
        sa.Column(
            "erp_salesperson_no", sa.Text(), nullable=False, unique=True
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )

    op.create_table(
        "territory_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "territory_id",
            UUID(as_uuid=True),
            sa.ForeignKey("territories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "territory_id"),
    )

    # -- Customers + sites -------------------------------------------------
    op.create_table(
        "customers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("erp_customer_no", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "aliases", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("market_segment", sa.Text(), nullable=False),
    )

    op.create_table(
        "customer_sites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "acreage_by_crop",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.UniqueConstraint("customer_id", "name"),
    )

    # -- Catalog -----------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sku", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("price_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "is_regulated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "services",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sku", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("pricing_model", sa.Text(), nullable=False),
        sa.Column("default_rate_usd", sa.Numeric(12, 2)),
        sa.Column("description", sa.Text()),
    )

    op.create_table(
        "regulated_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
        ),
        sa.Column("restriction_type", sa.Text(), nullable=False),
        sa.Column(
            "restricted_states",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("notes", sa.Text()),
    )

    # -- Rules -------------------------------------------------------------
    op.create_table(
        "red_flag_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_name", sa.Text(), nullable=False, unique=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )

    op.create_table(
        "approval_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_name", sa.Text(), nullable=False, unique=True),
        sa.Column("condition_description", sa.Text(), nullable=False),
        sa.Column("requires_role", sa.Text(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )

    # -- Inspections -------------------------------------------------------
    op.create_table(
        "customer_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_site_id",
            UUID(as_uuid=True),
            sa.ForeignKey("customer_sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("hives_placed", sa.Integer(), nullable=False),
        sa.Column("varroa_count", sa.Numeric(6, 2), nullable=False),
        sa.Column(
            "diseases_observed",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("pesticide_exposure_note", sa.Text()),
        sa.Column("narrative", sa.Text(), nullable=False),
    )

    # -- RAG corpus --------------------------------------------------------
    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )

    # -- Inbox + orders ----------------------------------------------------
    op.create_table(
        "synced_emails",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.Text(), nullable=False, unique=True),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column(
            "to_addresses",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_classified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("pollination_date", sa.Date()),
        sa.Column("total_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "source_email_id",
            UUID(as_uuid=True),
            sa.ForeignKey("synced_emails.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "order_line_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "service_id",
            UUID(as_uuid=True),
            sa.ForeignKey("services.id", ondelete="RESTRICT"),
        ),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_usd", sa.Numeric(12, 2), nullable=False),
        sa.CheckConstraint(
            "product_id IS NOT NULL OR service_id IS NOT NULL",
            name="line_item_product_or_service",
        ),
    )

    op.create_table(
        "order_state_transitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.Text()),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column(
            "actor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )


def downgrade() -> None:
    # Reverse FK order for clean drops.
    op.drop_table("order_state_transitions")
    op.drop_table("order_line_items")
    op.drop_table("orders")
    op.drop_table("synced_emails")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("customer_reports")
    op.drop_table("approval_rules")
    op.drop_table("red_flag_rules")
    op.drop_table("regulated_items")
    op.drop_table("services")
    op.drop_table("products")
    op.drop_table("customer_sites")
    op.drop_table("customers")
    op.drop_table("territory_assignments")
    op.drop_table("users")
    op.drop_table("territories")
    # Leave pgvector extension in place — other DBs on the same cluster may
    # use it, and dropping is cheap to redo if this migration re-runs.
