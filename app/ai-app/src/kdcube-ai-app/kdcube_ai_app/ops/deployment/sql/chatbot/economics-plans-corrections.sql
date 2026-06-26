-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 Elena Viter
--
-- Economics plans corrections (one-time, idempotent migration). Brings a
-- previously-deployed project schema onto the current `plans` naming + new
-- fields. Replace <SCHEMA> with the project schema
-- (e.g. kdcube_chatbot_<tenant>_<project>) before applying, mirroring
-- deploy-kdcube-proj-schema.sql.
--
-- Runs BEFORE the schema provision (deploy-kdcube-proj-schema.sql). Ordering
-- matters: the renames must happen before `CREATE TABLE IF NOT EXISTS` would
-- otherwise create empty new-named tables next to the populated old ones.
--
-- Every statement is guarded (to_regclass / IF EXISTS / IF NOT EXISTS), so on a
-- fresh schema (tables/schema absent) the whole file is a clean no-op and the
-- provision creates everything under the new names directly.
--
-- 0) Rename the economics tables to the `plans` naming. ALTER TABLE RENAME keeps
--    every dependent object (PK, indexes, FKs, triggers, CHECK constraints)
--    attached under its original name, so the re-run provision reconciles them
--    as no-ops (the DDL keeps those object names unchanged).
--      subscription_plans                    -> plans
--      user_subscriptions                    -> user_plans
--      user_subscription_period_budget       -> user_plan_period_budget
--      user_subscription_period_reservations -> user_plan_period_reservations
--      user_subscription_period_ledger       -> user_plan_period_ledger
--
-- 1) Rename the `payasyougo` quota policy -> `wallet`. HARD rename (no alias).
--    Only `plan_quota_policies` carries the literal. If `wallet` already exists
--    (reseed ran first), the stale `payasyougo` row is dropped.
--
-- 2) Add the durable RL monthly-window anchor mirror to `user_plans`.
--    `CREATE TABLE IF NOT EXISTS` never retrofits a column onto an existing
--    table, so an already-deployed row needs this explicit ALTER. Additive;
--    existing rows get NULL and are back-filled lazily on the next request.
--
-- 3) Relax the internal-row Stripe-id check so a stripe_customer_id may be
--    attached while the subscription is still internal (the Stripe checkout
--    customer-resolver attaches it pre-payment). Gate only stripe_subscription_id;
--    provider flips to 'stripe' with the subscription id on invoice-paid. Same
--    constraint name, relaxed body. `CREATE TABLE IF NOT EXISTS` never alters an
--    existing constraint, so an already-deployed table needs this explicit ALTER.

-- 0) table renames -> `plans` naming
DO $$
BEGIN
  IF to_regclass('<SCHEMA>.subscription_plans') IS NOT NULL
     AND to_regclass('<SCHEMA>.plans') IS NULL THEN
    ALTER TABLE <SCHEMA>.subscription_plans RENAME TO plans;
  END IF;

  IF to_regclass('<SCHEMA>.user_subscriptions') IS NOT NULL
     AND to_regclass('<SCHEMA>.user_plans') IS NULL THEN
    ALTER TABLE <SCHEMA>.user_subscriptions RENAME TO user_plans;
  END IF;

  IF to_regclass('<SCHEMA>.user_subscription_period_budget') IS NOT NULL
     AND to_regclass('<SCHEMA>.user_plan_period_budget') IS NULL THEN
    ALTER TABLE <SCHEMA>.user_subscription_period_budget RENAME TO user_plan_period_budget;
  END IF;

  IF to_regclass('<SCHEMA>.user_subscription_period_reservations') IS NOT NULL
     AND to_regclass('<SCHEMA>.user_plan_period_reservations') IS NULL THEN
    ALTER TABLE <SCHEMA>.user_subscription_period_reservations RENAME TO user_plan_period_reservations;
  END IF;

  IF to_regclass('<SCHEMA>.user_subscription_period_ledger') IS NOT NULL
     AND to_regclass('<SCHEMA>.user_plan_period_ledger') IS NULL THEN
    ALTER TABLE <SCHEMA>.user_subscription_period_ledger RENAME TO user_plan_period_ledger;
  END IF;
END $$;

-- 1) payasyougo -> wallet
DO $$
BEGIN
  IF to_regclass('<SCHEMA>.plan_quota_policies') IS NULL THEN
    RETURN;
  END IF;
  IF EXISTS (
    SELECT 1 FROM <SCHEMA>.plan_quota_policies WHERE plan_id = 'payasyougo'
  ) THEN
    IF EXISTS (
      SELECT 1 FROM <SCHEMA>.plan_quota_policies WHERE plan_id = 'wallet'
    ) THEN
      DELETE FROM <SCHEMA>.plan_quota_policies WHERE plan_id = 'payasyougo';
    ELSE
      UPDATE <SCHEMA>.plan_quota_policies
         SET plan_id = 'wallet', updated_at = NOW()
       WHERE plan_id = 'payasyougo';
    END IF;
  END IF;
END $$;

-- 2) RL monthly-window anchor mirror (on the renamed user_plans)
ALTER TABLE IF EXISTS <SCHEMA>.user_plans
  ADD COLUMN IF NOT EXISTS rl_month_anchor_at timestamptz NULL;

-- 3) relax the internal-row Stripe-id check (on the renamed user_plans)
ALTER TABLE IF EXISTS <SCHEMA>.user_plans
  DROP CONSTRAINT IF EXISTS chk_cp_us_stripe_ids_internal_null;
DO $$
BEGIN
  IF to_regclass('<SCHEMA>.user_plans') IS NOT NULL THEN
    ALTER TABLE <SCHEMA>.user_plans
      ADD CONSTRAINT chk_cp_us_stripe_ids_internal_null CHECK (
        provider <> 'internal'
        OR stripe_subscription_id IS NULL
      );
  END IF;
END $$;
