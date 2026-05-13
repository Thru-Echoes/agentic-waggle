-- Test schema for the Waggle seed generator.
--
-- Matches the column set the seed inserts into. Kept separate from the
-- alembic migrations so the test suite can tear down and recreate the
-- schema per test without coupling to migration history.
--
-- Constraints (UNIQUE, NOT NULL, FK) are deliberately tight so that
-- fail-loud tests (duplicate inserts, missing FKs) actually trigger
-- IntegrityError from Postgres.

CREATE EXTENSION IF NOT EXISTS vector;

-- Drop order (for reruns)
DROP TABLE IF EXISTS sku_search_entries CASCADE;
DROP TABLE IF EXISTS customer_search_entries CASCADE;
DROP TABLE IF EXISTS chat_turns CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS order_state_transitions CASCADE;
DROP TABLE IF EXISTS order_line_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS red_flag_rules CASCADE;
DROP TABLE IF EXISTS approval_rules CASCADE;
DROP TABLE IF EXISTS regulated_items CASCADE;
DROP TABLE IF EXISTS chunks CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS customer_reports CASCADE;
DROP TABLE IF EXISTS synced_emails CASCADE;
DROP TABLE IF EXISTS customer_sites CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS territory_assignments CASCADE;
DROP TABLE IF EXISTS territories CASCADE;
DROP TABLE IF EXISTS services CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- Reference / directory tables

CREATE TABLE territories (
    id UUID PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('apiarist', 'ops_manager', 'admin')),
    erp_salesperson_no TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE territory_assignments (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    territory_id UUID NOT NULL REFERENCES territories(id) ON DELETE CASCADE,
    UNIQUE (user_id, territory_id)
);

CREATE TABLE customers (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    erp_customer_no TEXT NOT NULL UNIQUE,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    market_segment TEXT NOT NULL
);

CREATE TABLE customer_sites (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    acreage_by_crop JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (customer_id, name)
);

CREATE TABLE products (
    id UUID PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price_usd NUMERIC(12, 2) NOT NULL,
    is_regulated BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE services (
    id UUID PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    pricing_model TEXT NOT NULL,
    default_rate_usd NUMERIC(12, 2),
    description TEXT
);

CREATE TABLE regulated_items (
    id UUID PRIMARY KEY,
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    restriction_type TEXT NOT NULL,
    restricted_states JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT
);

CREATE TABLE red_flag_rules (
    id UUID PRIMARY KEY,
    rule_name TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE approval_rules (
    id UUID PRIMARY KEY,
    rule_name TEXT NOT NULL UNIQUE,
    condition_description TEXT NOT NULL,
    requires_role TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Customer reports and the document/chunk RAG corpus

CREATE TABLE customer_reports (
    id UUID PRIMARY KEY,
    customer_site_id UUID NOT NULL REFERENCES customer_sites(id) ON DELETE CASCADE,
    author_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    visit_date DATE NOT NULL,
    hives_placed INT NOT NULL,
    varroa_count NUMERIC(6, 2) NOT NULL,
    diseases_observed JSONB NOT NULL DEFAULT '[]'::jsonb,
    pesticide_exposure_note TEXT,
    narrative TEXT NOT NULL,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE documents (
    id UUID PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id UUID NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding vector(384) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Email intake

CREATE TABLE synced_emails (
    id UUID PRIMARY KEY,
    external_id TEXT NOT NULL UNIQUE,
    thread_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_addresses JSONB NOT NULL DEFAULT '[]'::jsonb,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    is_classified BOOLEAN NOT NULL DEFAULT FALSE
);

-- Orders + line items + state transitions

CREATE TABLE orders (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    pollination_date DATE,
    total_usd NUMERIC(12, 2) NOT NULL,
    source_email_id UUID REFERENCES synced_emails(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE order_line_items (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id) ON DELETE RESTRICT,
    service_id UUID REFERENCES services(id) ON DELETE RESTRICT,
    sku TEXT NOT NULL,
    quantity INT NOT NULL,
    unit_price_usd NUMERIC(12, 2) NOT NULL,
    CHECK (product_id IS NOT NULL OR service_id IS NOT NULL)
);

CREATE TABLE order_state_transitions (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
    transitioned_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Sales copilot — conversations + chat turns. Mirrors alembic
-- migration 003_add_copilot_conversations. Server defaults are kept
-- so the tests can INSERT without specifying timestamps.
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    salesperson_id UUID REFERENCES users(id) ON DELETE SET NULL,
    active_account_id UUID REFERENCES customers(id) ON DELETE SET NULL,
    title TEXT,
    model_used TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_conversations_salesperson_id ON conversations (salesperson_id);
CREATE INDEX ix_conversations_updated_at ON conversations (updated_at);

CREATE TABLE chat_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_used JSONB NOT NULL DEFAULT '[]'::jsonb,
    model TEXT,
    tokens_input INT NOT NULL DEFAULT 0,
    tokens_output INT NOT NULL DEFAULT 0,
    cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0,
    latency_ms INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (role IN ('user', 'assistant', 'system', 'tool'))
);

-- Semantic matcher — customer_search_entries + sku_search_entries. Mirrors
-- alembic migration 004_semantic_matcher. Server defaults kept so tests can
-- INSERT without specifying timestamps.
CREATE TABLE customer_search_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    entry_text  TEXT NOT NULL,
    source      TEXT NOT NULL,
    embedding   VECTOR(384) NOT NULL,
    tsv         TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', entry_text)) STORED NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT customer_search_entries_source_check
        CHECK (source IN ('name', 'alias', 'site'))
);
CREATE INDEX customer_search_entries_embedding_ivfflat
    ON customer_search_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 32);
CREATE INDEX customer_search_entries_tsv_gin
    ON customer_search_entries USING gin (tsv);
CREATE INDEX customer_search_entries_customer_id_idx
    ON customer_search_entries (customer_id);

CREATE TABLE sku_search_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID NULL REFERENCES products(id) ON DELETE CASCADE,
    service_id  UUID NULL REFERENCES services(id) ON DELETE CASCADE,
    entry_text  TEXT NOT NULL,
    source      TEXT NOT NULL,
    embedding   VECTOR(384) NOT NULL,
    tsv         TSVECTOR GENERATED ALWAYS AS
                  (to_tsvector('english', entry_text)) STORED NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sku_search_entries_source_check
        CHECK (source IN ('product_name','product_code','product_alias',
                          'service_name','service_code','service_alias')),
    CONSTRAINT sku_search_entries_exactly_one_target
        CHECK ((product_id IS NULL) <> (service_id IS NULL))
);
CREATE INDEX sku_search_entries_embedding_ivfflat
    ON sku_search_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 16);
CREATE INDEX sku_search_entries_tsv_gin
    ON sku_search_entries USING gin (tsv);
CREATE INDEX sku_search_entries_product_id_idx
    ON sku_search_entries (product_id);
CREATE INDEX sku_search_entries_service_id_idx
    ON sku_search_entries (service_id);
CREATE INDEX ix_chat_turns_conversation_id ON chat_turns (conversation_id, created_at);
