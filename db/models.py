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
from sqlalchemy import CheckConstraint, Column, ForeignKey, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
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
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column("tags", JSONB, nullable=False, server_default="[]"),
    )

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


# ---------------------------------------------------------------------------
# Sales copilot: conversations + chat_turns
# ---------------------------------------------------------------------------


class Conversation(SQLModel, table=True):
    """One sales-copilot session.

    `salesperson_id` is nullable so anonymous demo sessions persist too.
    `active_account_id` tracks which customer the conversation is currently
    "anchored" on (last referenced or explicitly switched). `model_used`
    is the most recent turn's model for observability rollups; per-turn
    breakdown lives on chat_turns.
    """

    __tablename__: ClassVar[str] = "conversations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    salesperson_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="users.id", nullable=True
    )
    active_account_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="customers.id", nullable=True
    )
    title: Optional[str] = Field(default=None, nullable=True)
    model_used: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)

    turns: Mapped[list["ChatTurn"]] = Relationship(back_populates="conversation")


class ChatTurn(SQLModel, table=True):
    """One turn in a copilot conversation.

    Append-only — turns are never edited after write. ``role`` is one of
    ``user``, ``assistant``, ``system``, ``tool`` (CHECK-constrained at
    the DB).

    Variable-shape payloads land in JSONB:
      - ``tool_calls``    — list of {tool_name, arguments, result_excerpt,
                            latency_ms, tokens} records.
      - ``evidence_used`` — verbatim quotes the assistant cited as
                            grounding for its response.

    Per-turn metrics (``tokens_input``, ``tokens_output``, ``cost_usd``,
    ``latency_ms``) feed the agent-metrics admin dashboard.
    """

    __tablename__: ClassVar[str] = "chat_turns"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="chat_turns_role_check",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(
        foreign_key="conversations.id", nullable=False
    )
    role: str = Field(..., nullable=False)
    text: str = Field(..., nullable=False)
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column("tool_calls", JSONB, nullable=False, server_default="[]"),
    )
    evidence_used: list[str] = Field(
        default_factory=list,
        sa_column=Column("evidence_used", JSONB, nullable=False, server_default="[]"),
    )
    model: Optional[str] = Field(default=None, nullable=True)
    tokens_input: int = Field(default=0, nullable=False)
    tokens_output: int = Field(default=0, nullable=False)
    cost_usd: Decimal = Field(default=Decimal("0"), nullable=False)
    latency_ms: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)

    conversation: Mapped[Optional["Conversation"]] = Relationship(back_populates="turns")


# ---------------------------------------------------------------------------
# Semantic matcher: customer_search_entries + sku_search_entries
# ---------------------------------------------------------------------------


class CustomerSearchEntry(SQLModel, table=True):
    """One indexed search entry — a customer name, alias, or site name.

    Hybrid retrieval (pgvector cosine + Postgres FTS) ranks rows; LLM
    disambiguator picks one. Many entries point to one customer.

    Source values: 'name' | 'alias' | 'site' (CHECK-constrained at the DB).

    The `embedding` and `tsv` columns are managed by raw DDL in the migration
    (pgvector VECTOR(384) and GENERATED ALWAYS AS tsvector). They are not
    declared here to avoid SQLModel trying to re-create them via the ORM.

    Note: Customer.search_entries is intentionally NOT declared — search
    entries are a provider-layer concern; the Customer model never navigates
    to its entries (entries are queried directly via match_customer).
    """

    __tablename__: ClassVar[str] = "customer_search_entries"
    __table_args__ = (
        CheckConstraint(
            "source IN ('name', 'alias', 'site')",
            name="customer_search_entries_source_check",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_id: uuid.UUID = Field(
        sa_column=Column(
            "customer_id",
            PGUUID(as_uuid=True),
            ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    entry_text: str = Field(
        ...,
        nullable=False,
        description="The text surface form indexed for hybrid retrieval (FTS + pgvector cosine).",
    )
    # NOTE: Literal["name", "alias", "site"] is the correct Python type but
    # SQLModel's mapper cannot introspect Literal for table models (issubclass
    # fails on Literal args). The DB-level CHECK constraint is the authoritative
    # enforcement. Use Literal in the Pydantic response schemas instead.
    source: str = Field(
        ...,
        nullable=False,
        description=(
            "Origin of this entry: customer name, alias, or site name. "
            "Valid values: 'name' | 'alias' | 'site' (enforced by DB CHECK constraint)."
        ),
    )
    # pgvector embedding + generated tsv handled by raw DDL — not modeled on
    # the Python side to avoid SQLModel re-declaring them with wrong types.
    created_at: datetime = Field(default_factory=utc_now, nullable=False)

    customer: Mapped[Optional["Customer"]] = Relationship()


class SkuSearchEntry(SQLModel, table=True):
    """One indexed search entry for a product or service.

    Exactly one of product_id / service_id is non-null (enforced by a CHECK
    constraint at the DB level: (product_id IS NULL) <> (service_id IS NULL)).

    Source values: 'product_name' | 'product_code' | 'product_alias' |
                   'service_name' | 'service_code' | 'service_alias'
                   (CHECK-constrained at the DB).

    The `embedding` and `tsv` columns are managed by raw DDL in the migration
    (pgvector VECTOR(384) and GENERATED ALWAYS AS tsvector). They are not
    declared here to avoid SQLModel trying to re-create them via the ORM.
    """

    __tablename__: ClassVar[str] = "sku_search_entries"
    __table_args__ = (
        CheckConstraint(
            "source IN ('product_name','product_code','product_alias',"
            "'service_name','service_code','service_alias')",
            name="sku_search_entries_source_check",
        ),
        CheckConstraint(
            "(product_id IS NULL) <> (service_id IS NULL)",
            name="sku_search_entries_exactly_one_target",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    product_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            "product_id",
            PGUUID(as_uuid=True),
            ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    service_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            "service_id",
            PGUUID(as_uuid=True),
            ForeignKey("services.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    entry_text: str = Field(
        ...,
        nullable=False,
        description="The text surface form indexed for hybrid retrieval (FTS + pgvector cosine).",
    )
    # NOTE: Literal[...] is the correct Python type but SQLModel's mapper
    # cannot introspect Literal for table models. DB-level CHECK constraint
    # is the authoritative enforcement. Use Literal in Pydantic response schemas.
    source: str = Field(
        ...,
        nullable=False,
        description=(
            "Origin of this entry: product name/code/alias or service name/code/alias. "
            "Valid values: 'product_name' | 'product_code' | 'product_alias' | "
            "'service_name' | 'service_code' | 'service_alias' "
            "(enforced by DB CHECK constraint)."
        ),
    )
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


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
    "Conversation",
    "ChatTurn",
    "CustomerSearchEntry",
    "SkuSearchEntry",
]
