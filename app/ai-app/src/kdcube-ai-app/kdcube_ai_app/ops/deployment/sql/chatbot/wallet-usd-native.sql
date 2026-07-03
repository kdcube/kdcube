-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 Elena Viter
--
-- Wallet USD-native (cents) one-time, idempotent migration. Adds the authoritative
-- USD-in-cents columns to a previously-deployed project schema:
--   user_lifetime_credits   -> purchased_cents, spent_cents
--   user_token_reservations -> usd_reserved_cents, actual_spent_cents
-- Replace <SCHEMA> with the project schema (e.g. kdcube_<tenant>_<project>) before
-- applying, mirroring deploy-kdcube-proj-schema.sql.
--
-- Runs BEFORE the schema provision (deploy-kdcube-proj-schema.sql). The provision's
-- CREATE TABLE carries these columns for fresh schemas; this migration carries them
-- for existing ones. The back-fill runs ONCE, inside the column guard, converting the
-- legacy token columns at the sonnet-4-5 reference rate (15 USD / 1M output tokens)
-- under which all existing credits were purchased -- keeping the migration
-- balance-neutral. The token columns are kept as display-only.
--
-- Each block is guarded on table existence AND column absence, so the file is a clean
-- no-op on a fresh schema (tables absent, pre-provision) and on a re-run (columns
-- present).
--
-- TEMPORARY: remove this file (and its entry in apply_project_migrations) once every
-- environment has the cents columns.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = '<SCHEMA>'
          AND table_name = 'user_lifetime_credits'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = '<SCHEMA>'
          AND table_name = 'user_lifetime_credits'
          AND column_name = 'spent_cents'
    ) THEN
        ALTER TABLE <SCHEMA>.user_lifetime_credits
            ADD COLUMN purchased_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN spent_cents     BIGINT NOT NULL DEFAULT 0;

        UPDATE <SCHEMA>.user_lifetime_credits
            SET purchased_cents = ROUND(lifetime_usd_purchased * 100),
                spent_cents     = ROUND(lifetime_tokens_consumed * 15.0 / 1e6 * 100);

        ALTER TABLE <SCHEMA>.user_lifetime_credits
            ADD CONSTRAINT chk_cp_ulc_cents_nonneg
            CHECK (purchased_cents >= 0 AND spent_cents >= 0);
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = '<SCHEMA>'
          AND table_name = 'user_token_reservations'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = '<SCHEMA>'
          AND table_name = 'user_token_reservations'
          AND column_name = 'usd_reserved_cents'
    ) THEN
        ALTER TABLE <SCHEMA>.user_token_reservations
            ADD COLUMN usd_reserved_cents BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN actual_spent_cents BIGINT DEFAULT NULL;

        UPDATE <SCHEMA>.user_token_reservations
            SET usd_reserved_cents = ROUND(tokens_reserved * 15.0 / 1e6 * 100),
                actual_spent_cents = CASE WHEN tokens_used IS NULL THEN NULL
                                          ELSE ROUND(tokens_used * 15.0 / 1e6 * 100) END;

        ALTER TABLE <SCHEMA>.user_token_reservations
            ADD CONSTRAINT chk_cp_utr_cents_nonneg
            CHECK (usd_reserved_cents >= 0 AND (actual_spent_cents IS NULL OR actual_spent_cents >= 0));
    END IF;
END $$;
