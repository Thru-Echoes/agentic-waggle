"""Pydantic domain models for the seed generator.

These are the intermediate representations that the seed generators produce.
Each generator's `persist()` function converts these into table INSERTs
against the Waggle schema (see `db/models.py`).

Keeping generation decoupled from persistence means:
  * Generators can be unit-tested without a DB.
  * When the SQL schema shifts, only the persistence mapper changes.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MarketSegment = Literal[
    "ORCHARD_ALMOND",
    "ORCHARD_TREE_FRUIT",
    "ORCHARD_BERRY",
    "HONEY_WHOLESALER",
    "SPECIALTY_FOOD",
    "MEAD_WINERY",
    "PEER_BEEKEEPER",
]

UserRole = Literal["apiarist", "ops_manager", "admin"]

ProductCategory = Literal[
    "honey",
    "queens_and_bees",
    "equipment",
    "beeswax_products",
    "consumables",
    "treatments",
]

ServiceCategory = Literal["pollination"]

PricingModel = Literal[
    "per_hive_flat",
    "per_acre_per_week",
    "per_hive_per_day",
    "negotiated",
]

OrderState = Literal[
    "DRAFT",
    "EXTRACTED",
    "UNDER_REVIEW",
    "APPROVED",
    "SCHEDULED",
    "PACKED",
    "IN_PROGRESS",
    "FULFILLED",
    "INVOICED",
    "SYNCED_TO_ERP",
    "CANCELLED",
]

DiseaseCode = Literal["AFB", "EFB", "NOSEMA", "SHB", "TROPILAELAPS", "NONE"]

RedFlagScope = Literal["order", "customer", "site", "user"]


class Region(BaseModel):
    model_config = ConfigDict(strict=True)

    code: str = Field(..., description="Short identifier, e.g. 'central_valley'")
    name: str
    primary_crops: list[str]
    peak_start_month: int = Field(..., ge=1, le=12)
    peak_end_month: int = Field(..., ge=1, le=12)


class Apiarist(BaseModel):
    email: str
    name: str
    role: UserRole
    erp_apiarist_no: str
    region_codes: list[str]


class Customer(BaseModel):
    name: str
    erp_customer_no: str
    market_segment: MarketSegment
    aliases: list[str] = Field(default_factory=list)


class CustomerSite(BaseModel):
    customer_name: str  # reference by name; resolved to FK at persistence
    name: str
    region_code: str
    acreage_by_crop: dict[str, int] = Field(default_factory=dict)
    bloom_start_month: int | None = None
    bloom_end_month: int | None = None
    latitude: float | None = None
    longitude: float | None = None


class Product(BaseModel):
    sku: str
    category: ProductCategory
    name: str
    description: str | None = None
    price_usd: Decimal
    is_regulated: bool = False


class Service(BaseModel):
    sku: str
    category: ServiceCategory
    name: str
    pricing_model: PricingModel
    default_rate_usd: Decimal | None = None
    description: str | None = None


class OrderLineItem(BaseModel):
    sku: str
    quantity: int
    unit_price_usd: Decimal


class Order(BaseModel):
    customer_name: str
    apiarist_email: str
    state: OrderState
    pollination_date: date | None = None
    total_usd: Decimal
    line_items: list[OrderLineItem]
    source_email_ref: str | None = None  # ref to Email.external_id if EXTRACTED


class Email(BaseModel):
    external_id: str  # mimics Gmail message id
    thread_id: str
    account_id: str
    from_address: str
    to_addresses: list[str]
    subject: str
    body_text: str
    received_at: datetime
    attachments: list[dict] = Field(default_factory=list)


class InspectionReport(BaseModel):
    customer_name: str
    site_name: str
    author_email: str
    visit_date: date
    hives_placed: int
    varroa_count: Decimal
    diseases_observed: list[DiseaseCode]
    pesticide_exposure_note: str | None = None
    narrative: str


class RegulatedItem(BaseModel):
    # product_sku is None for category-level rules (e.g. "any VFD-required
    # antibiotic") that do not map to a specific SKU. The empty-string
    # workaround was removed — None means "no concrete SKU" explicitly.
    product_sku: str | None = None
    restriction_type: Literal[
        "state_import_permit",
        "vfd_required",
        "epa_label_restriction",
        "ttb_permit_analog",  # rare; kept for schema parity
    ]
    restricted_states: list[str] = Field(default_factory=list)
    notes: str | None = None


class RedFlagRule(BaseModel):
    rule_name: str
    scope: RedFlagScope
    description: str
    severity: Literal["info", "warn", "block"]


class ApprovalRule(BaseModel):
    rule_name: str
    condition_description: str
    requires_role: Literal["apiarist", "ops_manager", "admin", "none", "reject"]
