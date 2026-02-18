-- =========================================
-- drop-kdcube-control-plane.sql
-- Aggressive cleanup for legacy + current control plane objects.
-- Safe to run before a fresh deploy.
-- =========================================

-- =========================================
-- Drop view(s)
-- =========================================
DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.tenant_project_budget_status') IS NOT NULL THEN
        DROP VIEW IF EXISTS kdcube_control_plane.tenant_project_budget_status;
    END IF;
END $$;

-- =========================================
-- Drop triggers (guarded by table existence)
-- =========================================
DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_tier_overrides') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_uto_updated_at
          ON kdcube_control_plane.user_tier_overrides;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_lifetime_credits') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_ulc_updated_at
          ON kdcube_control_plane.user_lifetime_credits;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_token_reservations') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_utr_updated_at
          ON kdcube_control_plane.user_token_reservations;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_quota_policies') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_uqp_updated_at
          ON kdcube_control_plane.user_quota_policies;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.application_budget_policies') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_abp_updated_at
          ON kdcube_control_plane.application_budget_policies;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.tenant_project_budget') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_budget_updated_at
          ON kdcube_control_plane.tenant_project_budget;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_subscription_budget') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_sub_budget_updated_at
          ON kdcube_control_plane.user_subscription_budget;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_subscription_budget_settings') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_sub_budget_settings_updated_at
          ON kdcube_control_plane.user_subscription_budget_settings;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_subscription_period_budget') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_sub_period_budget_updated_at
          ON kdcube_control_plane.user_subscription_period_budget;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.user_subscriptions') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_us_updated_at
          ON kdcube_control_plane.user_subscriptions;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('kdcube_control_plane.external_economics_events') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS trg_cp_ext_econ_events_updated_at
          ON kdcube_control_plane.external_economics_events;
    END IF;
END $$;

-- =========================================
-- Drop function(s)
-- =========================================
DROP FUNCTION IF EXISTS kdcube_control_plane.update_updated_at() CASCADE;

-- =========================================
-- Drop indexes
-- (Indexes drop automatically with tables; listed explicitly for clarity.)
-- =========================================

-- user_tier_overrides
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_uto_lookup;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_uto_expires;

-- user_lifetime_credits
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_ulc_lookup;

-- user_token_reservations
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_utr_active;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_utr_expires;

-- user_quota_policies
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_uqp_lookup;

-- application_budget_policies
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_abp_lookup;

-- tenant_project_budget
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_budget_balance;

-- tenant_project_budget_reservations
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_budget_resv_active;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_budget_resv_lookup;

-- tenant_project_budget_ledger
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_budget_ledger_tenant_project_time;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_budget_ledger_reservation;

-- user_subscription_budget (legacy)
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_budget_lookup;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_budget_resv_active;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_budget_resv_lookup;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_budget_ledger_tp_user_time;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_budget_ledger_reservation;

-- user_subscription_budget_settings
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_budget_settings_lookup;

-- user_subscription_period_budget
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_period_budget_lookup;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_period_resv_active;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_period_resv_lookup;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_period_ledger_tp_user_time;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_sub_period_ledger_reservation;

-- user_subscriptions
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_us_due_internal;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_us_provider_status;
DROP INDEX IF EXISTS kdcube_control_plane.uq_cp_us_stripe_sub_id;
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_us_stripe_customer;

-- external_economics_events
DROP INDEX IF EXISTS kdcube_control_plane.idx_cp_ext_econ_events_lookup;

-- =========================================
-- Drop tables (child -> parent to avoid FK issues)
-- =========================================

-- External economics events (independent)
DROP TABLE IF EXISTS kdcube_control_plane.external_economics_events;

-- Subscription period budgets (current)
DROP TABLE IF EXISTS kdcube_control_plane.user_subscription_period_ledger;
DROP TABLE IF EXISTS kdcube_control_plane.user_subscription_period_reservations;
DROP TABLE IF EXISTS kdcube_control_plane.user_subscription_period_budget;

-- Subscription budgets (legacy)
DROP TABLE IF EXISTS kdcube_control_plane.user_subscription_budget_ledger;
DROP TABLE IF EXISTS kdcube_control_plane.user_subscription_budget_reservations;
DROP TABLE IF EXISTS kdcube_control_plane.user_subscription_budget;

-- Subscription settings + subscriptions
DROP TABLE IF EXISTS kdcube_control_plane.user_subscription_budget_settings;
DROP TABLE IF EXISTS kdcube_control_plane.user_subscriptions;

-- Token reservations depend on user_lifetime_credits
DROP TABLE IF EXISTS kdcube_control_plane.user_token_reservations;

-- Credits & overrides (independent from each other, but drop after reservations)
DROP TABLE IF EXISTS kdcube_control_plane.user_lifetime_credits;
DROP TABLE IF EXISTS kdcube_control_plane.user_tier_overrides;

-- Budget tables (reservations/ledger depend on tenant_project_budget)
DROP TABLE IF EXISTS kdcube_control_plane.tenant_project_budget_ledger;
DROP TABLE IF EXISTS kdcube_control_plane.tenant_project_budget_reservations;
DROP TABLE IF EXISTS kdcube_control_plane.tenant_project_budget;

-- Policies
DROP TABLE IF EXISTS kdcube_control_plane.application_budget_policies;
DROP TABLE IF EXISTS kdcube_control_plane.user_quota_policies;

-- =========================================
-- Drop type(s)
-- =========================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'budget_reservation_status'
          AND n.nspname = 'kdcube_control_plane'
    ) THEN
        DROP TYPE kdcube_control_plane.budget_reservation_status;
    END IF;
END $$;

-- =========================================
-- Drop schema (aggressive cleanup)
-- =========================================
DROP SCHEMA IF EXISTS kdcube_control_plane CASCADE;

-- NOTE:
-- We do NOT drop pgcrypto here because it is commonly shared across schemas.
-- If you truly want to remove it (only if nothing else uses it):
-- DROP EXTENSION IF EXISTS pgcrypto;
