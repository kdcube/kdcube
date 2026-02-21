-- =========================================
-- deploy-kdcube-control-plane.sql
-- Control Plane Schema (Approach A: split plan + personal credits)
-- =========================================

CREATE SCHEMA IF NOT EXISTS kdcube_control_plane;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================================
-- Common updated_at trigger function
-- =========================================
CREATE OR REPLACE FUNCTION kdcube_control_plane.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =========================================
-- USER PLAN OVERRIDES (temporary plan quota overrides)
--  - expires
--  - does NOT store lifetime credits
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.user_plan_overrides (
    tenant  VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,

    -- Tier Override Limits (NULL = use base plan)
    max_concurrent     INTEGER DEFAULT NULL,
    requests_per_day   INTEGER DEFAULT NULL,
    requests_per_month INTEGER DEFAULT NULL,
    total_requests     INTEGER DEFAULT NULL,
    tokens_per_hour    BIGINT  DEFAULT NULL,
    tokens_per_day     BIGINT  DEFAULT NULL,
    tokens_per_month   BIGINT  DEFAULT NULL,

    -- Expiry
    expires_at TIMESTAMPTZ DEFAULT NULL,

    -- Optional grant tracking (separate from credits purchases!)
    grant_id         VARCHAR(255) DEFAULT NULL,
    grant_amount_usd NUMERIC(10, 2) DEFAULT NULL,
    grant_notes      TEXT DEFAULT NULL,

    -- Status + metadata
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (tenant, project, user_id)
);

COMMENT ON TABLE kdcube_control_plane.user_plan_overrides IS
  'Temporary plan overrides (admin grants). Expires via expires_at.';

CREATE INDEX IF NOT EXISTS idx_cp_upo_lookup
  ON kdcube_control_plane.user_plan_overrides(tenant, project, user_id)
  WHERE active = TRUE;

CREATE INDEX IF NOT EXISTS idx_cp_upo_expires
  ON kdcube_control_plane.user_plan_overrides(expires_at)
  WHERE active = TRUE AND expires_at IS NOT NULL;

DROP TRIGGER IF EXISTS trg_cp_upo_updated_at ON kdcube_control_plane.user_plan_overrides;
CREATE TRIGGER trg_cp_upo_updated_at
  BEFORE UPDATE ON kdcube_control_plane.user_plan_overrides
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

-- =========================================
-- USER LIFETIME CREDITS (personal funds)
--  - NEVER expire
--  - deplete on use
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.user_lifetime_credits (
    tenant  VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,

    lifetime_tokens_purchased BIGINT NOT NULL DEFAULT 0,
    lifetime_tokens_consumed  BIGINT NOT NULL DEFAULT 0,

    -- Aggregate lifetime purchase (for reporting)
    lifetime_usd_purchased NUMERIC(10, 2) NOT NULL DEFAULT 0,

    -- Last purchase snapshot (for debugging / UI)
    last_purchase_id         VARCHAR(255) DEFAULT NULL,
    last_purchase_amount_usd NUMERIC(10, 2) DEFAULT NULL,
    last_purchase_notes      TEXT DEFAULT NULL,

    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (tenant, project, user_id),

    CONSTRAINT chk_cp_ulc_consumed_nonneg
        CHECK (lifetime_tokens_consumed >= 0)

--     CONSTRAINT chk_cp_ulc_nonneg
--       CHECK (lifetime_tokens_purchased >= 0 AND lifetime_tokens_consumed >= 0),

--     CONSTRAINT chk_cp_ulc_consumed_le_purchased
--       CHECK (lifetime_tokens_consumed <= lifetime_tokens_purchased),

--     CONSTRAINT chk_cp_ulc_usd_nonneg
--       CHECK (lifetime_usd_purchased >= 0)
);

COMMENT ON TABLE kdcube_control_plane.user_lifetime_credits IS
  'Personal lifetime credits: purchased tokens that deplete on use. Never expire.';

CREATE INDEX IF NOT EXISTS idx_cp_ulc_lookup
  ON kdcube_control_plane.user_lifetime_credits(tenant, project, user_id)
  WHERE active = TRUE;

DROP TRIGGER IF EXISTS trg_cp_ulc_updated_at ON kdcube_control_plane.user_lifetime_credits;
CREATE TRIGGER trg_cp_ulc_updated_at
  BEFORE UPDATE ON kdcube_control_plane.user_lifetime_credits
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

-- =========================================
-- USER TOKEN RESERVATIONS (Personal Credits)
-- Prevent concurrent overspending of lifetime credits.
-- FK -> user_lifetime_credits
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.user_token_reservations (
    tenant VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,

    reservation_id VARCHAR(255) NOT NULL,  -- your turn_id

    bundle_id VARCHAR(255) DEFAULT NULL,
    notes TEXT DEFAULT NULL,

    tokens_reserved BIGINT NOT NULL CHECK (tokens_reserved >= 0),
    tokens_used BIGINT DEFAULT NULL CHECK (tokens_used IS NULL OR tokens_used >= 0),

    status VARCHAR(32) NOT NULL DEFAULT 'reserved'
      CHECK (status IN ('reserved', 'committed', 'released')),

    expires_at TIMESTAMPTZ NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    committed_at TIMESTAMPTZ DEFAULT NULL,
    released_at TIMESTAMPTZ DEFAULT NULL,

    PRIMARY KEY (tenant, project, user_id, reservation_id),

    CONSTRAINT chk_cp_utr_used_le_reserved
      CHECK (tokens_used IS NULL OR tokens_used <= tokens_reserved),

    CONSTRAINT fk_cp_utr_user_credits
      FOREIGN KEY (tenant, project, user_id)
      REFERENCES kdcube_control_plane.user_lifetime_credits(tenant, project, user_id)
      ON DELETE CASCADE
);

COMMENT ON TABLE kdcube_control_plane.user_token_reservations IS
  'In-flight reservations for lifetime credits to prevent concurrent overspend.';

CREATE INDEX IF NOT EXISTS idx_cp_utr_active
  ON kdcube_control_plane.user_token_reservations(tenant, project, user_id, expires_at)
  WHERE status = 'reserved';

CREATE INDEX IF NOT EXISTS idx_cp_utr_reservation_id
  ON kdcube_control_plane.user_token_reservations(tenant, project, reservation_id);

CREATE INDEX IF NOT EXISTS idx_cp_utr_expires
  ON kdcube_control_plane.user_token_reservations(expires_at)
  WHERE status = 'reserved';

DROP TRIGGER IF EXISTS trg_cp_utr_updated_at ON kdcube_control_plane.user_token_reservations;
CREATE TRIGGER trg_cp_utr_updated_at
  BEFORE UPDATE ON kdcube_control_plane.user_token_reservations
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

-- =========================================
-- PLAN QUOTA POLICIES (base limits per plan_id)
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.plan_quota_policies (
    tenant VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    plan_id VARCHAR(255) NOT NULL,

    max_concurrent INTEGER DEFAULT NULL,
    requests_per_day INTEGER DEFAULT NULL,
    requests_per_month INTEGER DEFAULT NULL,
    total_requests INTEGER DEFAULT NULL,
    tokens_per_hour BIGINT DEFAULT NULL,
    tokens_per_day BIGINT DEFAULT NULL,
    tokens_per_month BIGINT DEFAULT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(255) DEFAULT NULL,
    notes TEXT DEFAULT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (tenant, project, plan_id)
);


CREATE INDEX IF NOT EXISTS idx_cp_pqp_lookup
  ON kdcube_control_plane.plan_quota_policies(tenant, project, plan_id)
  WHERE active = TRUE;

DROP TRIGGER IF EXISTS trg_cp_pqp_updated_at ON kdcube_control_plane.plan_quota_policies;
CREATE TRIGGER trg_cp_pqp_updated_at
  BEFORE UPDATE ON kdcube_control_plane.plan_quota_policies
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

-- =========================================
-- APPLICATION BUDGET POLICIES (limits per provider)
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.application_budget_policies (
    tenant VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    provider VARCHAR(255) NOT NULL,

    usd_per_hour NUMERIC(10, 2) DEFAULT NULL,
    usd_per_day NUMERIC(10, 2) DEFAULT NULL,
    usd_per_month NUMERIC(10, 2) DEFAULT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(255) DEFAULT NULL,
    notes TEXT DEFAULT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (tenant, project, provider)
);

CREATE INDEX IF NOT EXISTS idx_cp_abp_lookup
  ON kdcube_control_plane.application_budget_policies(tenant, project, provider)
  WHERE active = TRUE;

DROP TRIGGER IF EXISTS trg_cp_abp_updated_at ON kdcube_control_plane.application_budget_policies;
CREATE TRIGGER trg_cp_abp_updated_at
  BEFORE UPDATE ON kdcube_control_plane.application_budget_policies
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

-- =========================================
-- Tenant/Project Budget Balance + Reservations + Ledger
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.tenant_project_budget (
    tenant VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,

    balance_cents BIGINT NOT NULL DEFAULT 0,

    lifetime_added_cents BIGINT NOT NULL DEFAULT 0,
    lifetime_spent_cents BIGINT NOT NULL DEFAULT 0,

    reserved_cents BIGINT NOT NULL DEFAULT 0,
    overdraft_limit_cents BIGINT DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT DEFAULT NULL,

    PRIMARY KEY (tenant, project),

    CONSTRAINT chk_cp_budget_reserved_nonneg CHECK (reserved_cents >= 0)
);

-- Backfill columns for existing schemas
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'kdcube_control_plane'
          AND table_name = 'tenant_project_budget'
          AND column_name = 'reserved_cents'
    ) THEN
        ALTER TABLE kdcube_control_plane.tenant_project_budget
            ADD COLUMN reserved_cents BIGINT NOT NULL DEFAULT 0;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'kdcube_control_plane'
          AND table_name = 'tenant_project_budget'
          AND column_name = 'overdraft_limit_cents'
    ) THEN
        ALTER TABLE kdcube_control_plane.tenant_project_budget
            ADD COLUMN overdraft_limit_cents BIGINT DEFAULT 0;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_cp_budget_reserved_nonneg'
          AND conrelid = 'kdcube_control_plane.tenant_project_budget'::regclass
    ) THEN
        ALTER TABLE kdcube_control_plane.tenant_project_budget
            ADD CONSTRAINT chk_cp_budget_reserved_nonneg CHECK (reserved_cents >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_cp_budget_balance
  ON kdcube_control_plane.tenant_project_budget(tenant, project, balance_cents);

DROP TRIGGER IF EXISTS trg_cp_budget_updated_at ON kdcube_control_plane.tenant_project_budget;
CREATE TRIGGER trg_cp_budget_updated_at
  BEFORE UPDATE ON kdcube_control_plane.tenant_project_budget
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

-- Reservation status enum
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'budget_reservation_status') THEN
        CREATE TYPE kdcube_control_plane.budget_reservation_status AS ENUM (
            'active',
            'committed',
            'released',
            'expired'
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS kdcube_control_plane.tenant_project_budget_reservations (
    reservation_id UUID PRIMARY KEY,

    tenant  VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,

    bundle_id VARCHAR(255) DEFAULT NULL,
    provider  VARCHAR(255) DEFAULT NULL,
    user_id   VARCHAR(255) DEFAULT NULL,
    request_id VARCHAR(255) DEFAULT NULL,

    amount_cents BIGINT NOT NULL,
    actual_spent_cents BIGINT DEFAULT NULL,

    status kdcube_control_plane.budget_reservation_status NOT NULL DEFAULT 'active',

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    committed_at TIMESTAMPTZ DEFAULT NULL,
    released_at  TIMESTAMPTZ DEFAULT NULL,
    notes        TEXT  DEFAULT NULL,

    CONSTRAINT fk_cp_budget_resv_budget
      FOREIGN KEY (tenant, project)
      REFERENCES kdcube_control_plane.tenant_project_budget (tenant, project)
      ON DELETE CASCADE,

    CONSTRAINT chk_cp_budget_resv_amount_pos CHECK (amount_cents > 0),
    CONSTRAINT chk_cp_budget_resv_actual_nonneg CHECK (actual_spent_cents IS NULL OR actual_spent_cents >= 0)
);

CREATE INDEX IF NOT EXISTS idx_cp_budget_resv_active
  ON kdcube_control_plane.tenant_project_budget_reservations (tenant, project, status, expires_at);

CREATE INDEX IF NOT EXISTS idx_cp_budget_resv_lookup
  ON kdcube_control_plane.tenant_project_budget_reservations (tenant, project, reservation_id);

CREATE TABLE IF NOT EXISTS kdcube_control_plane.tenant_project_budget_ledger (
    id BIGSERIAL PRIMARY KEY,

    tenant  VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,

    amount_cents BIGINT NOT NULL, -- signed
    kind VARCHAR(64) NOT NULL,
    note TEXT DEFAULT NULL,

    reservation_id UUID DEFAULT NULL,
    bundle_id VARCHAR(255) DEFAULT NULL,
    provider  VARCHAR(255) DEFAULT NULL,
    user_id   VARCHAR(255) DEFAULT NULL,
    request_id VARCHAR(255) DEFAULT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_cp_budget_ledger_budget
      FOREIGN KEY (tenant, project)
      REFERENCES kdcube_control_plane.tenant_project_budget (tenant, project)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cp_budget_ledger_tenant_project_time
  ON kdcube_control_plane.tenant_project_budget_ledger (tenant, project, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cp_budget_ledger_reservation
  ON kdcube_control_plane.tenant_project_budget_ledger (reservation_id);

CREATE INDEX IF NOT EXISTS idx_cp_budget_ledger_request
  ON kdcube_control_plane.tenant_project_budget_ledger (tenant, project, request_id);

CREATE INDEX IF NOT EXISTS idx_cp_budget_resv_request
  ON kdcube_control_plane.tenant_project_budget_reservations (tenant, project, request_id);

CREATE INDEX IF NOT EXISTS idx_cp_budget_ledger_shortfall
  ON kdcube_control_plane.tenant_project_budget_ledger (tenant, project, created_at DESC)
  WHERE note LIKE 'shortfall:%';

CREATE INDEX IF NOT EXISTS idx_cp_budget_ledger_shortfall_user
  ON kdcube_control_plane.tenant_project_budget_ledger (tenant, project, user_id, created_at DESC)
  WHERE note LIKE 'shortfall:%';

CREATE INDEX IF NOT EXISTS idx_cp_budget_ledger_shortfall_bundle
  ON kdcube_control_plane.tenant_project_budget_ledger (tenant, project, bundle_id, created_at DESC)
  WHERE note LIKE 'shortfall:%';

CREATE OR REPLACE VIEW kdcube_control_plane.tenant_project_budget_absorption AS
SELECT
    tenant,
    project,
    date_trunc('day', created_at) AS day,
    date_trunc('month', created_at) AS month,
    CASE
        WHEN note LIKE 'shortfall:wallet_subscription%' THEN 'wallet_subscription'
        WHEN note LIKE 'shortfall:wallet_paid%' THEN 'wallet_paid'
        WHEN note LIKE 'shortfall:wallet_plan%' THEN 'wallet_plan'
        WHEN note LIKE 'shortfall:subscription_overage%' THEN 'subscription_overage'
        WHEN note LIKE 'shortfall:free_plan%' THEN 'free_plan'
        ELSE 'shortfall'
    END AS reason,
    SUM(-amount_cents) AS absorbed_cents,
    COUNT(*) AS events
FROM kdcube_control_plane.tenant_project_budget_ledger
WHERE kind='spend' AND note LIKE 'shortfall:%'
GROUP BY tenant, project, day, month, reason;

CREATE OR REPLACE VIEW kdcube_control_plane.tenant_project_budget_absorption_detail AS
SELECT
    tenant,
    project,
    user_id,
    bundle_id,
    date_trunc('day', created_at) AS day,
    date_trunc('month', created_at) AS month,
    CASE
        WHEN note LIKE 'shortfall:wallet_subscription%' THEN 'wallet_subscription'
        WHEN note LIKE 'shortfall:wallet_paid%' THEN 'wallet_paid'
        WHEN note LIKE 'shortfall:wallet_plan%' THEN 'wallet_plan'
        WHEN note LIKE 'shortfall:subscription_overage%' THEN 'subscription_overage'
        WHEN note LIKE 'shortfall:free_plan%' THEN 'free_plan'
        ELSE 'shortfall'
    END AS reason,
    SUM(-amount_cents) AS absorbed_cents,
    COUNT(*) AS events
FROM kdcube_control_plane.tenant_project_budget_ledger
WHERE kind='spend' AND note LIKE 'shortfall:%'
GROUP BY tenant, project, user_id, bundle_id, day, month, reason;

CREATE OR REPLACE VIEW kdcube_control_plane.tenant_project_budget_status AS
SELECT
    tenant,
    project,
    balance_cents,
    reserved_cents,
    (balance_cents - reserved_cents) AS available_cents,
    overdraft_limit_cents,
    GREATEST(0, -(balance_cents - reserved_cents)) AS overdraft_used_cents,
    lifetime_added_cents,
    lifetime_spent_cents,
    created_at,
    updated_at,
    notes
FROM kdcube_control_plane.tenant_project_budget;

-- =========================================
-- USER SUBSCRIPTION PERIOD BUDGET (per-billing-cycle)
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.user_subscription_period_budget (
    tenant VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    period_key TEXT NOT NULL,

    period_start TIMESTAMPTZ NOT NULL,
    period_end   TIMESTAMPTZ NOT NULL,

    status TEXT NOT NULL DEFAULT 'open'
      CHECK (status IN ('open','closed')),

    balance_cents BIGINT NOT NULL DEFAULT 0,
    reserved_cents BIGINT NOT NULL DEFAULT 0,

    topup_cents BIGINT NOT NULL DEFAULT 0,
    rolled_over_cents BIGINT NOT NULL DEFAULT 0,

    closed_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT DEFAULT NULL,

    PRIMARY KEY (tenant, project, user_id, period_key),

    CONSTRAINT chk_cp_sub_period_reserved_nonneg CHECK (reserved_cents >= 0)
);

CREATE INDEX IF NOT EXISTS idx_cp_sub_period_budget_lookup
  ON kdcube_control_plane.user_subscription_period_budget(tenant, project, user_id, period_end DESC);

DROP TRIGGER IF EXISTS trg_cp_sub_period_budget_updated_at ON kdcube_control_plane.user_subscription_period_budget;
CREATE TRIGGER trg_cp_sub_period_budget_updated_at
  BEFORE UPDATE ON kdcube_control_plane.user_subscription_period_budget
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

CREATE TABLE IF NOT EXISTS kdcube_control_plane.user_subscription_period_reservations (
    reservation_id UUID PRIMARY KEY,

    tenant  VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    period_key TEXT NOT NULL,

    bundle_id VARCHAR(255) DEFAULT NULL,
    provider  VARCHAR(255) DEFAULT NULL,
    request_id VARCHAR(255) DEFAULT NULL,

    amount_cents BIGINT NOT NULL,
    actual_spent_cents BIGINT DEFAULT NULL,

    status kdcube_control_plane.budget_reservation_status NOT NULL DEFAULT 'active',

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    committed_at TIMESTAMPTZ DEFAULT NULL,
    released_at  TIMESTAMPTZ DEFAULT NULL,
    notes        TEXT  DEFAULT NULL,

    CONSTRAINT fk_cp_sub_period_resv_budget
      FOREIGN KEY (tenant, project, user_id, period_key)
      REFERENCES kdcube_control_plane.user_subscription_period_budget (tenant, project, user_id, period_key)
      ON DELETE CASCADE,

    CONSTRAINT chk_cp_sub_period_resv_amount_pos CHECK (amount_cents > 0),
    CONSTRAINT chk_cp_sub_period_resv_actual_nonneg CHECK (actual_spent_cents IS NULL OR actual_spent_cents >= 0)
);

CREATE INDEX IF NOT EXISTS idx_cp_sub_period_resv_active
  ON kdcube_control_plane.user_subscription_period_reservations (tenant, project, user_id, period_key, status, expires_at);

CREATE INDEX IF NOT EXISTS idx_cp_sub_period_resv_lookup
  ON kdcube_control_plane.user_subscription_period_reservations (tenant, project, user_id, period_key, reservation_id);

CREATE INDEX IF NOT EXISTS idx_cp_sub_period_resv_request
  ON kdcube_control_plane.user_subscription_period_reservations (tenant, project, request_id);

CREATE TABLE IF NOT EXISTS kdcube_control_plane.user_subscription_period_ledger (
    id BIGSERIAL PRIMARY KEY,

    tenant  VARCHAR(255) NOT NULL,
    project VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    period_key TEXT NOT NULL,

    amount_cents BIGINT NOT NULL, -- signed
    kind VARCHAR(64) NOT NULL,
    note TEXT DEFAULT NULL,

    reservation_id UUID DEFAULT NULL,
    bundle_id VARCHAR(255) DEFAULT NULL,
    provider  VARCHAR(255) DEFAULT NULL,
    request_id VARCHAR(255) DEFAULT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_cp_sub_period_ledger_budget
      FOREIGN KEY (tenant, project, user_id, period_key)
      REFERENCES kdcube_control_plane.user_subscription_period_budget (tenant, project, user_id, period_key)
      ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cp_sub_period_ledger_tp_user_time
  ON kdcube_control_plane.user_subscription_period_ledger (tenant, project, user_id, period_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cp_sub_period_ledger_reservation
  ON kdcube_control_plane.user_subscription_period_ledger (reservation_id);

CREATE INDEX IF NOT EXISTS idx_cp_sub_period_ledger_request
  ON kdcube_control_plane.user_subscription_period_ledger (tenant, project, request_id);

-- =========================================
-- SUBSCRIPTION PLANS (TIER + PRICE MAPPING)
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.subscription_plans (
    tenant text NOT NULL,
    project text NOT NULL,
    plan_id text NOT NULL,

    provider text NOT NULL DEFAULT 'internal',
    stripe_price_id text NULL,
    monthly_price_cents int NOT NULL DEFAULT 0,

    active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_by text NULL,
    notes text NULL,

    PRIMARY KEY (tenant, project, plan_id),

    CONSTRAINT chk_cp_pl_provider CHECK (provider IN ('internal','stripe')),
    CONSTRAINT chk_cp_pl_price_nonneg CHECK (monthly_price_cents >= 0),
    CONSTRAINT chk_cp_pl_stripe_price_req CHECK (
      provider <> 'stripe' OR stripe_price_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_cp_pl_provider_active
  ON kdcube_control_plane.subscription_plans (tenant, project, provider, active);

DROP INDEX IF EXISTS uq_cp_pl_stripe_price_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cp_pl_stripe_price_id
  ON kdcube_control_plane.subscription_plans (tenant, project, stripe_price_id)
  WHERE provider='stripe' AND stripe_price_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_cp_pl_updated_at ON kdcube_control_plane.subscription_plans;
CREATE TRIGGER trg_cp_pl_updated_at
  BEFORE UPDATE ON kdcube_control_plane.subscription_plans
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

-- =========================================
-- SUBSCRIPTIONS (PLAN RESOLUTION SNAPSHOT)
--  - plan_id points to subscription_plans
--  - plan/monthly_price_cents are denormalized from plan
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.user_subscriptions (
    tenant text NOT NULL,
    project text NOT NULL,
    user_id text NOT NULL,

    plan_id text NULL,
    status text NOT NULL,
    monthly_price_cents int NOT NULL DEFAULT 0,

    started_at timestamptz NOT NULL DEFAULT now(),
    next_charge_at timestamptz NULL,
    last_charged_at timestamptz NULL,

    provider text NOT NULL DEFAULT 'internal',
    stripe_customer_id text NULL,
    stripe_subscription_id text NULL,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (tenant, project, user_id),

    CONSTRAINT chk_cp_us_provider CHECK (provider IN ('internal','stripe')),
    CONSTRAINT chk_cp_us_status CHECK (status IN ('active','canceled','suspended')),
    CONSTRAINT chk_cp_us_price_nonneg CHECK (monthly_price_cents >= 0),

    CONSTRAINT chk_cp_us_stripe_ids_internal_null CHECK (
      provider <> 'internal'
      OR (stripe_customer_id IS NULL AND stripe_subscription_id IS NULL)
    ),

    CONSTRAINT fk_cp_us_plan
      FOREIGN KEY (tenant, project, plan_id)
      REFERENCES kdcube_control_plane.subscription_plans (tenant, project, plan_id)
      ON DELETE SET NULL
);

DROP TRIGGER IF EXISTS trg_cp_us_updated_at ON kdcube_control_plane.user_subscriptions;
CREATE TRIGGER trg_cp_us_updated_at
  BEFORE UPDATE ON kdcube_control_plane.user_subscriptions
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();

CREATE INDEX IF NOT EXISTS idx_cp_us_due_internal
  ON kdcube_control_plane.user_subscriptions (tenant, project, next_charge_at)
  WHERE provider='internal' AND status='active' AND next_charge_at IS NOT NULL AND monthly_price_cents > 0;

CREATE INDEX IF NOT EXISTS idx_cp_us_provider_status
  ON kdcube_control_plane.user_subscriptions (tenant, project, provider, status);

DROP INDEX IF EXISTS uq_cp_us_stripe_sub_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cp_us_stripe_sub_id
  ON kdcube_control_plane.user_subscriptions (stripe_subscription_id)
  WHERE provider='stripe' AND stripe_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cp_us_stripe_customer
  ON kdcube_control_plane.user_subscriptions (stripe_customer_id)
  WHERE provider='stripe' AND stripe_customer_id IS NOT NULL;

-- =========================================
-- Stripe idempotency / external economics events
-- =========================================
CREATE TABLE IF NOT EXISTS kdcube_control_plane.external_economics_events (
  source       TEXT NOT NULL,
  kind         TEXT NOT NULL,
  external_id  TEXT NOT NULL,

  tenant       TEXT NOT NULL,
  project      TEXT NOT NULL,
  user_id      TEXT NULL,

  amount_cents BIGINT NULL,
  tokens       BIGINT NULL,
  currency     TEXT NULL,

  status       TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','applied','failed','ignored')),

  stripe_event_id TEXT NULL,
  error        TEXT NULL,
  metadata     JSONB NULL,

  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  applied_at   TIMESTAMPTZ NULL,

  PRIMARY KEY (source, kind, external_id)
);

CREATE INDEX IF NOT EXISTS idx_cp_ext_econ_events_lookup
  ON kdcube_control_plane.external_economics_events (tenant, project, kind, status, created_at DESC);

DROP TRIGGER IF EXISTS trg_cp_ext_econ_events_updated_at ON kdcube_control_plane.external_economics_events;
CREATE TRIGGER trg_cp_ext_econ_events_updated_at
  BEFORE UPDATE ON kdcube_control_plane.external_economics_events
  FOR EACH ROW EXECUTE FUNCTION kdcube_control_plane.update_updated_at();
