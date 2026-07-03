-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 Elena Viter
--
-- Wallet USD-native (cents) migration for a previously-deployed (token-native)
-- project schema. Replace <SCHEMA> with the project schema (e.g.
-- kdcube_<tenant>_<project>) before applying, mirroring deploy-kdcube-proj-schema.sql.
-- Runs BEFORE the provision.
--
-- Target state, per table:
--   user_lifetime_credits     -> purchased_cents, spent_cents (token columns dropped)
--   user_token_reservations   -> renamed to user_credit_reservations;
--                                usd_reserved_cents, actual_spent_cents (token columns dropped)
--
-- Self-healing / idempotent. Each step is independently guarded so the file
-- converges on the target state from ANY intermediate state (fresh schema; the
-- original token-native schema; a partially-migrated schema that already has the
-- cents columns and/or the rename but still carries the token columns):
--   * add-cents-columns + back-fill runs ONLY when the cents columns are absent
--     (first pass). Back-fill is one-shot: once the cents columns exist they are
--     authoritative (the running code writes only cents), so it must never re-run
--     off the now-stale token columns.
--   * drop-token-columns runs whenever the token columns are still present,
--     independent of the cents-column state.
--   * the constraints and the rename are each ensured only if not already applied.
-- On a fresh schema the tables are absent (migration runs pre-provision), so every
-- block is a no-op and the CREATE in deploy-kdcube-proj-schema.sql builds the
-- target shape directly.
--
-- Back-fill uses the sonnet-4-5 reference rate (15 USD / 1M output tokens) — the
-- rate at which every credit to date was priced, so the cutover is balance-neutral.
--
-- TEMPORARY: remove this file (and its entry in apply_project_migrations) once every
-- environment is USD-native.

DO $$
BEGIN
    ----------------------------------------------------------------------------
    -- user_lifetime_credits: USD-in-cents (purchased_cents, spent_cents)
    ----------------------------------------------------------------------------
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = '<SCHEMA>' AND table_name = 'user_lifetime_credits'
    ) THEN
        -- add + back-fill cents columns, one-shot (only when absent)
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = '<SCHEMA>' AND table_name = 'user_lifetime_credits'
              AND column_name = 'spent_cents'
        ) THEN
            ALTER TABLE <SCHEMA>.user_lifetime_credits
                ADD COLUMN purchased_cents BIGINT NOT NULL DEFAULT 0,
                ADD COLUMN spent_cents     BIGINT NOT NULL DEFAULT 0;

            UPDATE <SCHEMA>.user_lifetime_credits
                SET purchased_cents = ROUND(lifetime_usd_purchased * 100),
                    spent_cents     = ROUND(lifetime_tokens_consumed * 15.0 / 1e6 * 100);
        END IF;

        -- ensure cents constraint
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_schema = '<SCHEMA>' AND table_name = 'user_lifetime_credits'
              AND constraint_name = 'chk_cp_ulc_cents_nonneg'
        ) THEN
            ALTER TABLE <SCHEMA>.user_lifetime_credits
                ADD CONSTRAINT chk_cp_ulc_cents_nonneg
                CHECK (purchased_cents >= 0 AND spent_cents >= 0);
        END IF;

        -- drop token columns whenever still present (independent of cents state)
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = '<SCHEMA>' AND table_name = 'user_lifetime_credits'
              AND column_name = 'lifetime_tokens_consumed'
        ) THEN
            ALTER TABLE <SCHEMA>.user_lifetime_credits
                DROP CONSTRAINT IF EXISTS chk_cp_ulc_consumed_nonneg,
                DROP COLUMN IF EXISTS lifetime_tokens_purchased,
                DROP COLUMN IF EXISTS lifetime_tokens_consumed;
        END IF;
    END IF;

    ----------------------------------------------------------------------------
    -- user_token_reservations -> user_credit_reservations: USD-in-cents
    ----------------------------------------------------------------------------
    -- rename first so every subsequent step operates on the canonical name
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = '<SCHEMA>' AND table_name = 'user_token_reservations'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = '<SCHEMA>' AND table_name = 'user_credit_reservations'
    ) THEN
        ALTER TABLE <SCHEMA>.user_token_reservations RENAME TO user_credit_reservations;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = '<SCHEMA>' AND table_name = 'user_credit_reservations'
    ) THEN
        -- add + back-fill cents columns, one-shot (only when absent)
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = '<SCHEMA>' AND table_name = 'user_credit_reservations'
              AND column_name = 'usd_reserved_cents'
        ) THEN
            ALTER TABLE <SCHEMA>.user_credit_reservations
                ADD COLUMN usd_reserved_cents BIGINT NOT NULL DEFAULT 0,
                ADD COLUMN actual_spent_cents BIGINT DEFAULT NULL;

            UPDATE <SCHEMA>.user_credit_reservations
                SET usd_reserved_cents = ROUND(tokens_reserved * 15.0 / 1e6 * 100),
                    actual_spent_cents = CASE WHEN tokens_used IS NULL THEN NULL
                                              ELSE ROUND(tokens_used * 15.0 / 1e6 * 100) END;
        END IF;

        -- ensure cents constraints
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_schema = '<SCHEMA>' AND table_name = 'user_credit_reservations'
              AND constraint_name = 'chk_cp_utr_cents_nonneg'
        ) THEN
            ALTER TABLE <SCHEMA>.user_credit_reservations
                ADD CONSTRAINT chk_cp_utr_cents_nonneg
                CHECK (usd_reserved_cents >= 0 AND (actual_spent_cents IS NULL OR actual_spent_cents >= 0));
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_schema = '<SCHEMA>' AND table_name = 'user_credit_reservations'
              AND constraint_name = 'chk_cp_utr_actual_le_reserved'
        ) THEN
            ALTER TABLE <SCHEMA>.user_credit_reservations
                ADD CONSTRAINT chk_cp_utr_actual_le_reserved
                CHECK (actual_spent_cents IS NULL OR actual_spent_cents <= usd_reserved_cents);
        END IF;

        -- drop token columns whenever still present (independent of cents state)
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = '<SCHEMA>' AND table_name = 'user_credit_reservations'
              AND column_name = 'tokens_reserved'
        ) THEN
            ALTER TABLE <SCHEMA>.user_credit_reservations
                DROP CONSTRAINT IF EXISTS chk_cp_utr_used_le_reserved,
                DROP COLUMN IF EXISTS tokens_reserved,
                DROP COLUMN IF EXISTS tokens_used;
        END IF;
    END IF;
END $$;
