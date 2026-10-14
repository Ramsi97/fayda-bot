-- schema.sql — reference DDL for the billing data layer (Postgres / Supabase).
--
-- This mirrors exactly what services/billing_service.py creates on startup via
-- init_db() (CREATE TABLE/INDEX IF NOT EXISTS). You do NOT need to run this by
-- hand — the bot self-bootstraps on first launch. It's kept here for reference
-- and for anyone inspecting the DB in the Supabase SQL editor.
--
-- Design rules:
--   * Every money/flag column is NOT NULL DEFAULT so a present-but-NULL value
--     can never silently break `balance >= amount` arithmetic.
--   * Money is BIGINT whole Birr (never NUMERIC/money — those map to Decimal
--     in asyncpg and break integer `//` math).
--   * owner_admin_id is nullable with no default: NULL means "unbound".
--   * transactions.id uses gen_random_uuid() (built into Supabase/PG15+, no
--     CREATE EXTENSION needed).

CREATE TABLE IF NOT EXISTS users (
    telegram_id     BIGINT PRIMARY KEY,
    name            TEXT        NOT NULL DEFAULT '',
    balance         BIGINT      NOT NULL DEFAULT 0,
    owner_admin_id  BIGINT,
    -- Per-user price override (whole Birr). NULL = fall back to the owner
    -- admin's price_per_download. Lets a reseller charge a specific user a
    -- custom per-card price (0 = free for that user). Nullable with no default
    -- so "unset" is distinct from "explicitly free (0)".
    price_override  BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_owner ON users (owner_admin_id);
-- Backfill the column for databases created before per-user pricing existed.
ALTER TABLE users ADD COLUMN IF NOT EXISTS price_override BIGINT;

CREATE TABLE IF NOT EXISTS admins (
    telegram_id         BIGINT PRIMARY KEY,
    role                TEXT        NOT NULL DEFAULT 'admin',
    ref_code            TEXT        UNIQUE,
    price_per_download  BIGINT      NOT NULL DEFAULT 10,
    active              BOOLEAN     NOT NULL DEFAULT TRUE,
    cbe_name            TEXT        NOT NULL DEFAULT '',
    cbe_number          TEXT        NOT NULL DEFAULT '',
    telebirr_name       TEXT        NOT NULL DEFAULT '',
    telebirr_number     TEXT        NOT NULL DEFAULT '',
    allowed_templates   TEXT        NOT NULL DEFAULT 'A,B',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE admins ADD COLUMN IF NOT EXISTS
    allowed_templates TEXT NOT NULL DEFAULT 'A,B';

CREATE TABLE IF NOT EXISTS transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id     BIGINT      NOT NULL,
    kind            TEXT        NOT NULL,   -- 'deposit' | 'debit'
    amount          BIGINT      NOT NULL,   -- whole Birr
    status          TEXT        NOT NULL,   -- 'pending' | 'approved' | 'rejected' | 'done'
    method          TEXT,
    receipt_file_id TEXT,
    note            TEXT,
    admin_id        BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,
    decided_by      BIGINT
);
CREATE INDEX IF NOT EXISTS idx_tx_telegram ON transactions (telegram_id);
CREATE INDEX IF NOT EXISTS idx_tx_admin    ON transactions (admin_id);
CREATE INDEX IF NOT EXISTS idx_tx_downline ON transactions (admin_id, kind, status);
