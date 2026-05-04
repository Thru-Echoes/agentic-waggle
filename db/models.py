"""SQLModel tables for Waggle.

Mirrors `tests/schema.sql` column-for-column. Every table has a UUID primary
key, tight NOT NULL / UNIQUE / FK constraints, and matches the seed
generators' raw-SQL expectations. Literal state fields use the string enums
in `db/enums.py`.

Conventions:
  * UUID PKs with `default_factory=uuid.uuid4`
  * TIMESTAMPTZ columns use `datetime` with `timezone.utc` helpers
  * `metadata_` Python attribute → `metadata` SQL column (SQLModel reserves `metadata`)
  * Decimal for monetary values
  * `list` / `dict` typed columns are backed by Postgres JSONB
  * `embedding` column on `chunks` is `vector(384)` via pgvector

Side effects: none at import time. Creating a row and flushing to a session
is the only way anything in this module writes to the DB.
"""
# pyright: reportMissingImports=false
# NOTE: `from __future__ import annotations` is intentionally NOT used here.
# SQLAlchemy 2.0's mapper inspects field annotations at class-creation time to
# resolve `Mapped[...]` relationship targets; PEP 563 string annotations break
# that resolution. This is the only file in the project where the future
# import is omitted — see CLAUDE.md for the project-wide convention.
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar, Optional

from enum import Enum as PyEnum

from pgvector.sqlalchemy import Vector  # type: ignore[import-not-found]
from sqlalchemy import CheckConstraint, Column, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped
from sqlmodel import Field, Relationship, SQLModel


def _enum_col(enum_cls: type[PyEnum], **kwargs: Any) -> Column:
    """Build a Column whose runtime representation matches the alembic schema.

    The migration declares enum columns as `sa.Text()` with a CHECK constraint
    listing the enum *values* (not names). Default SQLAlchemy `Enum(MyEnum)`
    serializes via the enum *name*, which would write APIARIST instead of
    apiarist. `values_callable` flips that to use the value side, and
    `native_enum=False` keeps the column as TEXT/VARCHAR (no Postgres ENUM
    type) so it stays compatible with the migration's CHECK-constraint
    representation.

    Side effects: none.
    """
    return Column(
        SAEnum(
            enum_cls,
            values_callable=lambda x: [str(e.value) for e in x],
            native_enum=False,
        ),
        **kwargs,
    )

from db.enums import (
    ApprovalRole,
    DocumentSourceType,
    MarketSegment,
    OrderState,
    PricingModel,
    ProductCategory,
    RedFlagScope,
    RedFlagSeverity,
    RestrictionType,
    ServiceCategory,
    UserRole,
)


def utc_now() -> datetime:
    """Return a timezone-aware now in UTC."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Org: territories, users, territory_assignments
# ---------------------------------------------------------------------------


class Territory(SQLModel, table=True):
    __tablename__: ClassVar[str] = "territories"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(..., sa_column_kwargs={"unique": True}, nullable=False)
    name: str = Field(..., nullable=False)
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )

    assignments: Mapped[list["TerritoryAssignment"]] = Relationship(back_populates="territory")


class User(SQLModel, table=True):
    __tablename__: ClassVar[str] = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(..., sa_column_kwargs={"unique": True}, nullable=False)
    name: str = Field(..., nullable=False)
    role: UserRole = Field(..., sa_column=_enum_col(UserRole, nullable=False))
    erp_salesperson_no: str = Field(
        ..., sa_column_kwargs={"unique": True}, nullable=False
    )
    is_active: bool = Field(default=True, nullable=False)

    assignments: Mapped[list["TerritoryAssignment"]] = Relationship(back_populates="user")
    customer_reports: Mapped[list["CustomerReport"]] = Relationship(back_populates="author")
    orders: Mapped[list["Order"]] = Relationship(back_populates="apiarist")


class TerritoryAssignment(SQLModel, table=True):
    __tablename__: ClassVar[str] = "territory_assignments"
    __table_args__ = (UniqueConstraint("user_id", "territory_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    territory_id: uuid.UUID = Field(foreign_key="territories.id", nullable=False)

    user: Mapped[Optional["User"]] = Relationship(back_populates="assignments")
    territory: Mapped[Optional["Territory"]] = Relationship(back_populates="assignments")


# ---------------------------------------------------------------------------
# Catalog: customers, customer_sites, products, services, regulated_items
# ---------------------------------------------------------------------------


class Customer(SQLModel, table=True):
    __tablename__: ClassVar[str] = "customers"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(..., nullable=False)
    erp_customer_no: str = Field(
        ..., sa_column_kwargs={"unique": True}, nullable=False
    )
    aliases: list[str] = Field(
        default_factory=list,
        sa_column=Column("aliases", JSONB, nullable=False, server_default="[]"),
    )
    market_segment: MarketSegment = Field(
        ..., sa_column=_enum_col(MarketSegment, nullable=False)
    )

    sites: Mapped[list["CustomerSite"]] = Relationship(back_populates="customer")
    orders: Mapped[list["Order"]] = Relationship(back_populates="customer")


class CustomerSite(SQLModel, table=True):
    __tablename__: ClassVar[str] = "customer_sites"
    __table_args__ = (UniqueConstraint("customer_id", "name"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_id: uuid.UUID = Field(foreign_key="customers.id", nullable=False)
    name: str = Field(..., nullable=False)
    acreage_by_crop: dict[str, int] = Field(
        default_factory=dict,
        sa_column=Column(
            "acreage_by_crop", JSONB, nullable=False, server_default="{}"
        ),
    )
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )

    customer: Mapped[Optional["Customer"]] = Relationship(back_populates="sites")
    customer_reports: Mapped[list["CustomerReport"]] = Relationship(back_populates="site")


class Product(SQLModel, table=True):
    __tablename__: ClassVar[str] = "products"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    sku: str = Field(..., sa_column_kwargs={"unique": True}, nullable=False)
    name: str = Field(..., nullable=False)
    category: ProductCategory = Field(
        ..., sa_column=_enum_col(ProductCategory, nullable=False)
    )
    price_usd: Decimal = Field(..., max_digits=12, decimal_places=2, nullable=False)
    is_regulated: bool = Field(default=False, nullable=False)

    regulated_items: Mapped[list["RegulatedItem"]] = Relationship(back_populates="product")


class Service(SQLModel, table=True):
    __tablename__: ClassVar[str] = "services"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    sku: str = Field(..., sa_column_kwargs={"unique": True}, nullable=False)
    name: str = Field(..., nullable=False)
    category: ServiceCategory = Field(
        ..., sa_column=_enum_col(ServiceCategory, nullable=False)
    )
    pricing_model: PricingModel = Field(
        ..., sa_column=_enum_col(PricingModel, nullable=False)
    )
    default_rate_usd: Optional[Decimal] = Field(
        default=None, max_digits=12, decimal_places=2
    )
    description: Optional[str] = None


class RegulatedItem(SQLModel, table=True):
    __tablename__: ClassVar[str] = "regulated_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    product_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="products.id"
    )
    restriction_type: RestrictionType = Field(
        ..., sa_column=_enum_col(RestrictionType, nullable=False)
    )
    restricted_states: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            "restricted_states", JSONB, nullable=False, server_default="[]"
        ),
    )
    notes: Optional[str] = None

    product: Mapped[Optional["Product"]] = Relationship(back_populates="regulated_items")


# ---------------------------------------------------------------------------
# Rules: red_flag_rules, approval_rules
# ---------------------------------------------------------------------------


class RedFlagRule(SQLModel, table=True):
    __tablename__: ClassVar[str] = "red_flag_rules"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    rule_name: str = Field(..., sa_column_kwargs={"unique": True}, nullable=False)
    scope: RedFlagScope = Field(
        ..., sa_column=_enum_col(RedFlagScope, nullable=False)
    )
    description: str = Field(..., nullable=False)
    severity: RedFlagSeverity = Field(
        ..., sa_column=_enum_col(RedFlagSeverity, nullable=False)
    )
    is_active: bool = Field(default=True, nullable=False)


class ApprovalRule(SQLModel, table=True):
    __tablename__: ClassVar[str] = "approval_rules"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    rule_name: str = Field(..., sa_column_kwargs={"unique": True}, nullable=False)
    condition_description: str = Field(..., nullable=False)
    requires_role: ApprovalRole = Field(
        ..., sa_column=_enum_col(ApprovalRole, nullable=False)
    )
    is_active: bool = Field(default=True, nullable=False)


# ---------------------------------------------------------------------------
# Inspections: customer_reports
# ---------------------------------------------------------------------------


class CustomerReport(SQLModel, table=True):
    __tablename__: ClassVar[str] = "customer_reports"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_site_id: uuid.UUID = Field(
        foreign_key="customer_sites.id", nullable=False
    )
    author_id: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    visit_date: date = Field(..., nullable=False)
    hives_placed: int = Field(..., nullable=False)
    varroa_count: Decimal = Field(
        ..., max_digits=6, decimal_places=2, nullable=False
    )
    diseases_observed: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            "diseases_observed", JSONB, nullable=False, server_default="[]"
        ),
    )
    pesticide_exposure_note: Optional[str] = None
    narrative: str = Field(..., nullable=False)

    site: Mapped[Optional["CustomerSite"]] = Relationship(back_populates="customer_reports")
    author: Mapped[Optional["User"]] = Relationship(back_populates="customer_reports")


# ---------------------------------------------------------------------------
# RAG: documents, chunks (with pgvector embedding)
# ---------------------------------------------------------------------------


class Document(SQLModel, table=True):
    __tablename__: ClassVar[str] = "documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source_type: DocumentSourceType = Field(
        ..., sa_column=_enum_col(DocumentSourceType, nullable=False)
    )
    source_id: uuid.UUID = Field(..., nullable=False)
    content: str = Field(..., nullable=False)
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )

    chunks: Mapped[list["Chunk"]] = Relationship(back_populates="document")


class Chunk(SQLModel, table=True):
    __tablename__: ClassVar[str] = "chunks"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", nullable=False)
    content: str = Field(..., nullable=False)
    embedding: list[float] = Field(
        ..., sa_column=Column("embedding", Vector(384), nullable=False)
    )
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )

    document: Mapped[Optional["Document"]] = Relationship(back_populates="chunks")


# ---------------------------------------------------------------------------
# Inbox + orders: synced_emails, orders, order_line_items, order_state_transitions
# ---------------------------------------------------------------------------


class SyncedEmail(SQLModel, table=True):
    __tablename__: ClassVar[str] = "synced_emails"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    external_id: str = Field(
        ..., sa_column_kwargs={"unique": True}, nullable=False
    )
    thread_id: str = Field(..., nullable=False)
    account_id: str = Field(..., nullable=False)
    from_address: str = Field(..., nullable=False)
    to_addresses: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            "to_addresses", JSONB, nullable=False, server_default="[]"
        ),
    )
    subject: str = Field(..., nullable=False)
    body_text: str = Field(..., nullable=False)
    received_at: datetime = Field(..., nullable=False)
    is_classified: bool = Field(default=False, nullable=False)

    orders: Mapped[list["Order"]] = Relationship(back_populates="source_email")


class Order(SQLModel, table=True):
    __tablename__: ClassVar[str] = "orders"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_id: uuid.UUID = Field(foreign_key="customers.id", nullable=False)
    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False)
    state: OrderState = Field(
        ..., sa_column=_enum_col(OrderState, nullable=False)
    )
    pollination_date: Optional[date] = None
    total_usd: Decimal = Field(..., max_digits=12, decimal_places=2, nullable=False)
    source_email_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="synced_emails.id"
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)

    customer: Mapped[Optional["Customer"]] = Relationship(back_populates="orders")
    apiarist: Mapped[Optional["User"]] = Relationship(back_populates="orders")
    source_email: Mapped[Optional["SyncedEmail"]] = Relationship(back_populates="orders")
    line_items: Mapped[list["OrderLineItem"]] = Relationship(back_populates="order")
    transitions: Mapped[list["OrderStateTransition"]] = Relationship(back_populates="order")


class OrderLineItem(SQLModel, table=True):
    __tablename__: ClassVar[str] = "order_line_items"
    __table_args__ = (
        CheckConstraint(
            "product_id IS NOT NULL OR service_id IS NOT NULL",
            name="line_item_product_or_service",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_id: uuid.UUID = Field(foreign_key="orders.id", nullable=False)
    product_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="products.id"
    )
    service_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="services.id"
    )
    sku: str = Field(..., nullable=False)
    quantity: int = Field(..., nullable=False)
    unit_price_usd: Decimal = Field(
        ..., max_digits=12, decimal_places=2, nullable=False
    )

    order: Mapped[Optional["Order"]] = Relationship(back_populates="line_items")


class OrderStateTransition(SQLModel, table=True):
    __tablename__: ClassVar[str] = "order_state_transitions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_id: uuid.UUID = Field(foreign_key="orders.id", nullable=False)
    from_state: Optional[OrderState] = Field(
        default=None, sa_column=_enum_col(OrderState, nullable=True)
    )
    to_state: OrderState = Field(
        ..., sa_column=_enum_col(OrderState, nullable=False)
    )
    actor_id: Optional[uuid.UUID] = Field(default=None, foreign_key="users.id")
    transitioned_at: datetime = Field(default_factory=utc_now, nullable=False)
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )

    order: Mapped[Optional["Order"]] = Relationship(back_populates="transitions")


__all__ = [
    "utc_now",
    "Territory",
    "User",
    "TerritoryAssignment",
    "Customer",
    "CustomerSite",
    "Product",
    "Service",
    "RegulatedItem",
    "RedFlagRule",
    "ApprovalRule",
    "CustomerReport",
    "Document",
    "Chunk",
    "SyncedEmail",
    "Order",
    "OrderLineItem",
    "OrderStateTransition",
]
