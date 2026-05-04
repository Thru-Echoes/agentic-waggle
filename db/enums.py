"""String enums for Waggle's domain model.

All enums inherit from `(str, Enum)` so Pydantic v2 serializes them as
their value directly (e.g., `"DRAFT"` rather than `<OrderState.DRAFT: 'DRAFT'>`).
Column types in Postgres are plain `TEXT` with `CHECK` constraints — we
don't use native PG enums because adding a new state would require a
migration to `ALTER TYPE`.

When a string Literal already exists for the same concept in `seed/models.py`,
the Enum values here must match — seed persistence writes those strings
directly and tests assert the distributions.
"""
from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    APIARIST = "apiarist"
    OPS_MANAGER = "ops_manager"
    ADMIN = "admin"


class MarketSegment(str, Enum):
    ORCHARD_ALMOND = "ORCHARD_ALMOND"
    ORCHARD_TREE_FRUIT = "ORCHARD_TREE_FRUIT"
    ORCHARD_BERRY = "ORCHARD_BERRY"
    HONEY_WHOLESALER = "HONEY_WHOLESALER"
    SPECIALTY_FOOD = "SPECIALTY_FOOD"
    MEAD_WINERY = "MEAD_WINERY"
    PEER_BEEKEEPER = "PEER_BEEKEEPER"


class ProductCategory(str, Enum):
    HONEY = "honey"
    QUEENS_AND_BEES = "queens_and_bees"
    EQUIPMENT = "equipment"
    BEESWAX_PRODUCTS = "beeswax_products"
    CONSUMABLES = "consumables"
    TREATMENTS = "treatments"


class ServiceCategory(str, Enum):
    POLLINATION = "pollination"


class PricingModel(str, Enum):
    PER_HIVE_FLAT = "per_hive_flat"
    PER_ACRE_PER_WEEK = "per_acre_per_week"
    PER_HIVE_PER_DAY = "per_hive_per_day"
    NEGOTIATED = "negotiated"


class OrderState(str, Enum):
    DRAFT = "DRAFT"
    EXTRACTED = "EXTRACTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PACKED = "PACKED"
    IN_PROGRESS = "IN_PROGRESS"
    FULFILLED = "FULFILLED"
    INVOICED = "INVOICED"
    SYNCED_TO_ERP = "SYNCED_TO_ERP"
    CANCELLED = "CANCELLED"


class RedFlagScope(str, Enum):
    ORDER = "order"
    CUSTOMER = "customer"
    SITE = "site"
    USER = "user"


class RedFlagSeverity(str, Enum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class ApprovalRole(str, Enum):
    APIARIST = "apiarist"
    OPS_MANAGER = "ops_manager"
    ADMIN = "admin"
    NONE = "none"
    REJECT = "reject"


class RestrictionType(str, Enum):
    STATE_IMPORT_PERMIT = "state_import_permit"
    VFD_REQUIRED = "vfd_required"
    EPA_LABEL_RESTRICTION = "epa_label_restriction"
    TTB_PERMIT_ANALOG = "ttb_permit_analog"


class DiseaseCode(str, Enum):
    AFB = "AFB"  # American foulbrood
    EFB = "EFB"  # European foulbrood
    NOSEMA = "NOSEMA"
    SHB = "SHB"  # Small hive beetle
    TROPILAELAPS = "TROPILAELAPS"
    NONE = "NONE"


class DocumentSourceType(str, Enum):
    CUSTOMER_REPORT = "customer_report"
    EMAIL = "email"
    PRODUCT_DATA_SHEET = "product_data_sheet"
    REGULATORY_DOC = "regulatory_doc"
