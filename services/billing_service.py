"""
Billing / points service.

Gives every Telegram user a **Birr balance** that is debited per generated ID
card and topped up via admin-approved deposits. Backed by **Postgres** (asyncpg)
— e.g. Supabase — so balances survive restarts and redeploys and are correct
across multiple instances (see app/config.py: DATABASE_URL and
AZURE_DEPLOYMENT.md).

Atomicity comes from single-statement SQL under READ COMMITTED rather than an
in-process lock, so this is correct even if several bot instances run at once:
  * charge  -> UPDATE ... WHERE balance >= amount (row lock + re-check) so a
               balance can never go negative under concurrent charges.
  * approve -> a status-gated UPDATE ... WHERE status='pending' claims the
               pending deposit (making double-tap a no-op) and credits the
               balance in the SAME transaction.

Tables
  users         telegram_id (PK, BIGINT); fields: name, balance (BIGINT Birr),
                owner_admin_id (nullable: NULL = unbound), created_at, updated_at.
  admins        telegram_id (PK, BIGINT); fields: role, ref_code (UNIQUE),
                price_per_download, active, cbe_name/cbe_number,
                telebirr_name/telebirr_number, created_at, updated_at.
  transactions  id (PK, UUID); fields: telegram_id, kind ('deposit'|'debit'),
                amount (BIGINT Birr), status ('pending'|'approved'|'rejected'|
                'done'), method, receipt_file_id, note, admin_id, created_at,
                decided_at, decided_by.

Admin rows are returned to callers as dicts shaped like the old MongoDB docs (an
`_id` alias = telegram_id and nested `cbe`/`telebirr` objects) via
`_admin_to_dict`, so handler/middleware code is unchanged.
"""

import uuid
from typing import Optional, Tuple
from urllib.parse import urlparse

import asyncpg

from app.config import settings


# Self-bootstrapping DDL (also shipped as schema.sql for reference). Idempotent —
# safe to run on every startup. Every money/flag column is NOT NULL DEFAULT so a
# present-but-NULL value can never silently break `balance >= amount` math; money
# columns are BIGINT (never NUMERIC, which maps to Decimal and breaks // math).
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id     BIGINT PRIMARY KEY,
    name            TEXT        NOT NULL DEFAULT '',
    balance         BIGINT      NOT NULL DEFAULT 0,
    owner_admin_id  BIGINT,
    -- Per-user price override (whole Birr). NULL = fall back to the owner
    -- admin's price_per_download. Lets a reseller charge a specific user a
    -- custom per-card price (0 = free for that user).
    price_override  BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_owner ON users (owner_admin_id);
-- Backfill the column for databases that existed before per-user pricing.
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
    -- Comma-separated list of templates this admin's users may access (e.g. 'A' or 'A,B').
    -- Super-admin can restrict an admin to a subset. Default = all templates.
    allowed_templates   TEXT        NOT NULL DEFAULT 'A,B',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Backfill the column for databases that existed before this migration.
ALTER TABLE admins ADD COLUMN IF NOT EXISTS
    allowed_templates TEXT NOT NULL DEFAULT 'A,B';

CREATE TABLE IF NOT EXISTS transactions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id     BIGINT      NOT NULL,
    kind            TEXT        NOT NULL,
    amount          BIGINT      NOT NULL,
    status          TEXT        NOT NULL,
    method          TEXT,
    receipt_file_id TEXT,
    note            TEXT,
    admin_id        BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ,
    decided_by      BIGINT
);
CREATE INDEX IF NOT EXISTS idx_tx_telegram ON transactions (telegram_id);
CREATE INDEX IF NOT EXISTS idx_tx_admin ON transactions (admin_id);
CREATE INDEX IF NOT EXISTS idx_tx_downline ON transactions (admin_id, kind, status);
"""


def _is_local(dsn: str) -> bool:
    """True for a localhost DSN (dev Postgres has no TLS; Supabase requires it)."""
    try:
        host = urlparse(dsn).hostname or ""
    except Exception:
        host = ""
    return host in ("", "localhost", "127.0.0.1", "::1")


class BillingService:
    def __init__(self, dsn: str, schema: Optional[str] = None):
        self.dsn = dsn
        # Optional dedicated schema (used by the integration test to isolate from
        # prod data); None keeps the default search_path (public).
        self.schema = schema
        self._pool: Optional[asyncpg.Pool] = None

    # ── Mapping helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _admin_to_dict(r: Optional[asyncpg.Record]) -> Optional[dict]:
        """Reassemble a flat admins row into the MongoDB-style doc callers expect:
        an `_id` alias (= telegram_id) and nested cbe/telebirr objects, with NULLs
        coalesced to "". Uses dict(r).get() so newly added columns have safe fallbacks."""
        if r is None:
            return None
        d = dict(r)
        return {
            "_id": d.get("telegram_id"),
            "telegram_id": d.get("telegram_id"),
            "role": d.get("role", "admin"),
            "ref_code": d.get("ref_code"),
            "price_per_download": d.get("price_per_download", 10),
            "active": d.get("active", True),
            "allowed_templates": (d.get("allowed_templates") or "A,B").strip(),
            "cbe": {"name": d.get("cbe_name") or "", "number": d.get("cbe_number") or ""},
            "telebirr": {
                "name": d.get("telebirr_name") or "",
                "number": d.get("telebirr_number") or "",
            },
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        }

    @staticmethod
    def _parse_deposit_id(deposit_id: str) -> Optional[uuid.UUID]:
        """Parse a callback-data deposit id into a UUID; invalid → None (safe)."""
        try:
            return uuid.UUID(str(deposit_id))
        except (ValueError, TypeError, AttributeError):
            return None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def init_db(self) -> None:
        """Open the pool, verify reachability, ensure tables/indexes, seed admins.

        Called once at startup. The SELECT 1 makes a bad DSN / unreachable DB
        fail loudly here instead of on the first user action (mirrors the old
        Mongo ping).
        """
        ssl = None if _is_local(self.dsn) else "require"

        # Pin the search_path for every pooled connection when a dedicated schema
        # is configured (test isolation). This MUST go through server_settings
        # (sent in the connection startup packet), not a `SET` in an init/setup
        # callback: asyncpg runs `RESET ALL` when a connection is released back
        # to the pool, which wipes any `SET search_path` and is not re-applied on
        # later acquires. A startup-packet value becomes the session default, so
        # `RESET ALL` resets *to* it instead of clobbering it. Pointing at a
        # not-yet-existing schema is fine — Postgres resolves it lazily at use.
        server_settings = (
            {"search_path": f'"{self.schema}", public'} if self.schema else None
        )

        self._pool = await asyncpg.create_pool(
            self.dsn,
            min_size=1,
            max_size=10,
            ssl=ssl,
            # asyncpg auto-prepares every query; disabling the cache keeps us safe
            # behind either Supabase pooler (transaction mode otherwise errors
            # with: prepared statement "__asyncpg_stmt_N__" does not exist).
            statement_cache_size=0,
            # Recycle idle conns — Supavisor drops idle backends.
            max_inactive_connection_lifetime=300,
            server_settings=server_settings,
        )

        async with self._pool.acquire() as conn:
            # Fail fast & loud if the DB is unreachable or the DSN is wrong.
            await conn.fetchval("SELECT 1")

            # The pinned search_path already points here (resolved lazily); create
            # the schema before any CREATE TABLE in _SCHEMA_SQL targets it.
            if self.schema:
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')

            await conn.execute(_SCHEMA_SQL)

            # Idempotent seed: upsert an admins row per super admin. role/ref_code
            # are only set on insert, so a pre-existing row keeps its config.
            super_ids = settings.super_admin_ids_ordered
            primary_super_admin = super_ids[0] if super_ids else None
            for tg_id in super_ids:
                await conn.execute(
                    """
                    INSERT INTO admins (telegram_id, role, ref_code, price_per_download, active)
                    VALUES ($1, 'super_admin', $2, $3, TRUE)
                    ON CONFLICT (telegram_id) DO UPDATE SET updated_at = now()
                    """,
                    tg_id,
                    str(tg_id),
                    int(settings.PRICE_PER_DOWNLOAD),
                )

            # Idempotent seed: upsert a 'vip' row for every VIP_IDS member.
            # VIPs are free-use accounts with no reseller capabilities. They get
            # no ref_code so they cannot invite users. Only role is set on insert
            # — a pre-existing row keeps whatever was manually configured.
            for tg_id in settings.vip_user_ids:
                # Skip if already a super-admin (super-admin wins)
                if tg_id in settings.super_admins:
                    continue
                await conn.execute(
                    """
                    INSERT INTO admins (telegram_id, role, ref_code, price_per_download, active)
                    VALUES ($1, 'vip', NULL, 0, TRUE)
                    ON CONFLICT (telegram_id) DO UPDATE
                        SET role = CASE WHEN admins.role = 'admin' THEN admins.role
                                        ELSE 'vip' END,
                            updated_at = now()
                    """,
                    tg_id,
                )

            # Backfill parity with the old Mongo init (no-ops on a fresh DB).
            if primary_super_admin is not None:
                await conn.execute(
                    "UPDATE users SET owner_admin_id = $1 WHERE owner_admin_id IS NULL",
                    primary_super_admin,
                )
                await conn.execute(
                    """
                    UPDATE transactions SET admin_id = $1
                     WHERE kind = 'deposit' AND status = 'pending' AND admin_id IS NULL
                    """,
                    primary_super_admin,
                )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ── Admin & Reseller Methods ──────────────────────────────────────────────

    async def get_admin(self, admin_id: int) -> Optional[dict]:
        row = await self._pool.fetchrow(
            "SELECT * FROM admins WHERE telegram_id = $1", admin_id
        )
        return self._admin_to_dict(row)

    async def get_admin_by_ref(self, ref_code: str) -> Optional[dict]:
        if not ref_code:
            return None
        row = await self._pool.fetchrow(
            "SELECT * FROM admins WHERE ref_code = $1", str(ref_code).strip()
        )
        return self._admin_to_dict(row)

    async def upsert_admin(
        self,
        admin_id: int,
        role: str = "admin",
        price: Optional[int] = None,
        active: bool = True,
    ) -> dict:
        """Create, re-activate, or promote an admin.

        On conflict the row is PROMOTED to the requested role (so re-adding an
        existing 'vip'/'agent' as a reseller actually changes their role) with
        two guards:

        * A 'super_admin' is never demoted — their role is preserved.
        * A row that is already 'admin'/'super_admin' keeps its configured
          price (we don't clobber a reseller's set price on re-add). A row being
          promoted from a lower role gets the provided/default price instead of
          inheriting e.g. a VIP's 0 price (which would make their users free).

        ref_code is backfilled via COALESCE so a promoted VIP (ref_code NULL)
        gets a working referral code, while an existing admin keeps theirs.
        """
        price_val = int(price) if price is not None else int(settings.PRICE_PER_DOWNLOAD)
        row = await self._pool.fetchrow(
            """
            INSERT INTO admins (telegram_id, role, ref_code, price_per_download, active)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                role = CASE WHEN admins.role = 'super_admin'
                            THEN admins.role ELSE EXCLUDED.role END,
                ref_code = COALESCE(admins.ref_code, EXCLUDED.ref_code),
                price_per_download = CASE
                    WHEN admins.role IN ('admin', 'super_admin')
                    THEN admins.price_per_download ELSE EXCLUDED.price_per_download END,
                active = EXCLUDED.active,
                updated_at = now()
            RETURNING *
            """,
            admin_id,
            role,
            str(admin_id),
            price_val,
            active,
        )
        return self._admin_to_dict(row)

    async def upsert_agent(self, agent_id: int, active: bool = True) -> dict:
        """Create or reactivate an agent.

        Agents can share a referral link and add VIP users, but they cannot set
        their own CBE/Telebirr accounts or prices. All VIPs they add are bound to
        the primary super admin so all payments flow to the super admin's accounts.

        A ref_code is assigned (= str(agent_id)) so they can still invite users via
        link if needed, but they earn nothing from those users.
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO admins (telegram_id, role, ref_code, price_per_download, active)
            VALUES ($1, 'agent', $2, $3, $4)
            ON CONFLICT (telegram_id)
            DO UPDATE SET role = CASE
                                    WHEN admins.role IN ('admin', 'super_admin') THEN admins.role
                                    ELSE 'agent'
                                  END,
                          active = EXCLUDED.active,
                          updated_at = now()
            RETURNING *
            """,
            agent_id,
            str(agent_id),
            int(settings.PRICE_PER_DOWNLOAD),
            active,
        )
        return self._admin_to_dict(row)

    async def upsert_vip(self, user_id: int, active: bool = True) -> dict:
        """Create or reactivate a VIP user (free user, price=0, no ref_code).

        VIPs have no downline/reseller capabilities, so ref_code is NULL.
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO admins (telegram_id, role, ref_code, price_per_download, active)
            VALUES ($1, 'vip', NULL, 0, $2)
            ON CONFLICT (telegram_id)
            DO UPDATE SET role = CASE WHEN admins.role IN ('admin', 'super_admin') THEN admins.role ELSE 'vip' END,
                          active = EXCLUDED.active,
                          updated_at = now()
            RETURNING *
            """,
            user_id,
            active,
        )
        return self._admin_to_dict(row)

    async def add_paying_vip(self, admin_id: int, user_id: int) -> bool:
        """Bind a user directly to `admin_id` as a paying VIP.

        A paying VIP bypasses the referral-link requirement: the admin manually
        grants them bot access. They are NOT free — they still need to deposit
        via CBE or Telebirr and pay per download at the admin's normal price.

        * Does NOT touch the admins table — the user remains in the users table.
        * Skips the bind if the target is already an admin/VIP (safety guard).
        * If the user is already bound to a different admin, the bind is kept
          as-is (first-wins semantics); returns True if the row exists either way.
        * Returns True if the user is now (or was already) bound to this admin,
          False if the target is an admin/VIP (binding not allowed).
        """
        # Guard: don't overwrite an existing admin/VIP row with a user row
        existing_role = await self._pool.fetchval(
            "SELECT role FROM admins WHERE telegram_id = $1", user_id
        )
        if existing_role is not None:
            # Target is already an admin or VIP — don't demote them
            return False

        # Ensure the user row exists (idempotent), then try to claim ownership.
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, balance, name)
            VALUES ($1, $2, '')
            ON CONFLICT (telegram_id) DO NOTHING
            """,
            user_id,
            int(settings.FREE_SIGNUP_BALANCE),
        )
        # Only set owner if not already bound (first-wins)
        await self._pool.execute(
            """
            UPDATE users SET owner_admin_id = $2, updated_at = now()
             WHERE telegram_id = $1 AND owner_admin_id IS NULL
            """,
            user_id,
            admin_id,
        )
        return True

    async def set_payment_fields(
        self,
        admin_id: int,
        cbe_name: Optional[str] = None,
        cbe_number: Optional[str] = None,
        telebirr_name: Optional[str] = None,
        telebirr_number: Optional[str] = None,
    ) -> bool:
        """Update whichever of the 4 payment fields are provided (non-None)."""
        sets = []
        args = []
        idx = 1
        if cbe_name is not None:
            sets.append(f"cbe_name = ${idx}")
            args.append(cbe_name)
            idx += 1
        if cbe_number is not None:
            sets.append(f"cbe_number = ${idx}")
            args.append(cbe_number)
            idx += 1
        if telebirr_name is not None:
            sets.append(f"telebirr_name = ${idx}")
            args.append(telebirr_name)
            idx += 1
        if telebirr_number is not None:
            sets.append(f"telebirr_number = ${idx}")
            args.append(telebirr_number)
            idx += 1
        sets.append("updated_at = now()")
        args.append(admin_id)
        row = await self._pool.fetchrow(
            f"UPDATE admins SET {', '.join(sets)} WHERE telegram_id = ${idx} "
            "RETURNING telegram_id",
            *args,
        )
        return row is not None

    async def set_price(self, admin_id: int, price: int) -> bool:
        row = await self._pool.fetchrow(
            "UPDATE admins SET price_per_download = $1, updated_at = now() "
            "WHERE telegram_id = $2 RETURNING telegram_id",
            int(price),
            admin_id,
        )
        return row is not None

    async def list_admins(self) -> list[dict]:
        rows = await self._pool.fetch("SELECT * FROM admins")
        return [self._admin_to_dict(r) for r in rows]

    async def remove_admin(self, admin_id: int) -> bool:
        """Soft remove admin by marking active=False."""
        row = await self._pool.fetchrow(
            "UPDATE admins SET active = FALSE, updated_at = now() "
            "WHERE telegram_id = $1 RETURNING telegram_id",
            admin_id,
        )
        return row is not None

    async def bind_user_owner(self, telegram_id: int, admin_id: int) -> bool:
        """Atomic first-wins binding. Refuses if the user is an admin or already
        bound. One race-free statement: admin → no insert → False; new non-admin →
        insert → True; existing-unbound → claim → True; existing-bound → False."""
        row = await self._pool.fetchrow(
            """
            INSERT INTO users (telegram_id, owner_admin_id, balance, name)
            SELECT $1, $2, $3, ''
             WHERE NOT EXISTS (SELECT 1 FROM admins WHERE telegram_id = $1)
            ON CONFLICT (telegram_id)
            DO UPDATE SET owner_admin_id = EXCLUDED.owner_admin_id, updated_at = now()
             WHERE users.owner_admin_id IS NULL
            RETURNING telegram_id
            """,
            telegram_id,
            admin_id,
            int(settings.FREE_SIGNUP_BALANCE),
        )
        return row is not None

    async def get_owner(self, telegram_id: int) -> Optional[int]:
        return await self._pool.fetchval(
            "SELECT owner_admin_id FROM users WHERE telegram_id = $1", telegram_id
        )

    async def get_role(self, telegram_id: int) -> str:
        """Returns 'super_admin' | 'admin' | 'agent' | 'vip' | 'user'.

        Priority: super_admin (env) > DB role check > 'user' fallback.
        'vip' rows in the DB are free-use accounts with no reseller powers.
        'agent' rows can only add VIPs; all payments go to the super admin.
        """
        if telegram_id in settings.super_admins:
            return "super_admin"
        row = await self._pool.fetchrow(
            "SELECT role, active FROM admins WHERE telegram_id = $1", telegram_id
        )
        if row and row["active"]:
            if row["role"] == "super_admin":
                return "super_admin"
            if row["role"] == "admin":
                return "admin"
            if row["role"] == "agent":
                return "agent"
            if row["role"] == "vip":
                return "vip"
        return "user"

    async def get_price_for_user(self, telegram_id: int) -> int:
        """Returns the price (Birr) charged per download for this user.

        * Super-admins → always FREE (price = 0)
        * Active admins, agents, and VIPs (role='admin'|'agent'|'vip') → FREE (price = 0)
        * Deactivated ex-admins/agents → fall through to normal user pricing
        * Regular users → their per-user override if set, else their owner-admin's
          price, else the global default
        """
        # 1. Super-admins are free.
        if telegram_id in settings.super_admins:
            return 0

        # 2. Active admins and VIPs are free.
        row = await self._pool.fetchrow(
            "SELECT active, role FROM admins WHERE telegram_id = $1", telegram_id
        )
        if row is not None and row["active"]:
            return 0  # covers role IN ('admin', 'super_admin', 'vip')

        # 3. Per-user override wins; else owner-admin's price; else global default.
        #    LEFT JOIN so a user with an override but no admin owner still resolves.
        row = await self._pool.fetchrow(
            """
            SELECT u.price_override, a.price_per_download AS owner_price, a.role AS owner_role
              FROM users u
              LEFT JOIN admins a ON a.telegram_id = u.owner_admin_id
             WHERE u.telegram_id = $1
            """,
            telegram_id,
        )
        if row is not None:
            if row["price_override"] is not None:
                return int(row["price_override"])
            if row["owner_price"] is not None:
                if row["owner_role"] == "agent":
                    return int(settings.PRICE_PER_DOWNLOAD)
                return int(row["owner_price"])

        # 4. Fallback.
        return int(settings.PRICE_PER_DOWNLOAD)

    async def set_user_price(
        self,
        telegram_id: int,
        price: Optional[int],
        require_owner: Optional[int] = None,
    ) -> bool:
        """Set or clear a single user's per-card price override.

        `price` is whole Birr (>= 0) to pin a custom price for this user (0 = free
        for them), or None to clear the override so they revert to their owner
        admin's price. When `require_owner` is set (a reseller admin), the update
        only lands if the user is actually in that admin's downline
        (owner_admin_id = require_owner); super admins pass require_owner=None to
        edit anyone. Returns True iff a matching user row was updated.
        """
        val = None if price is None else int(price)
        row = await self._pool.fetchrow(
            """
            UPDATE users SET price_override = $2, updated_at = now()
             WHERE telegram_id = $1
               AND ($3::bigint IS NULL OR owner_admin_id = $3)
            RETURNING telegram_id
            """,
            telegram_id,
            val,
            require_owner,
        )
        return row is not None

    async def get_user_price_override(self, telegram_id: int) -> Optional[int]:
        """Return the user's per-user price override (Birr), or None if unset."""
        val = await self._pool.fetchval(
            "SELECT price_override FROM users WHERE telegram_id = $1", telegram_id
        )
        return int(val) if val is not None else None

    # ── Template permission helpers ────────────────────────────────────────────

    async def set_allowed_templates(self, admin_id: int, templates: str) -> bool:
        """Set the templates this admin's users may access (e.g. 'A' or 'A,B').

        Super-admin calls this to restrict (or restore) a reseller admin.
        `templates` is a comma-separated string of uppercase letters.
        Returns True if the admin row existed and was updated.
        """
        cleaned = ",".join(
            t.strip().upper() for t in templates.split(",") if t.strip()
        ) or "A"
        row = await self._pool.fetchrow(
            "UPDATE admins SET allowed_templates = $1, updated_at = now() "
            "WHERE telegram_id = $2 RETURNING telegram_id",
            cleaned,
            admin_id,
        )
        return row is not None

    async def get_allowed_templates(self, telegram_id: int) -> list[str]:
        """Return the list of templates available to `telegram_id`.

        * Admins/super-admins always get ALL templates (they are free & trusted).
        * Regular users get the allowed_templates of their owner admin.
        * Unbound users (or admins not in DB yet) get all templates as fallback.
        """
        if telegram_id in settings.super_admins:
            return ["A", "B"]

        # Check if user is an active admin (all templates).
        active = await self._pool.fetchval(
            "SELECT active FROM admins WHERE telegram_id = $1", telegram_id
        )
        if active:
            return ["A", "B"]

        # Look up owner admin's allowed_templates.
        raw = await self._pool.fetchval(
            """
            SELECT a.allowed_templates
              FROM users u
              JOIN admins a ON a.telegram_id = u.owner_admin_id
             WHERE u.telegram_id = $1
            """,
            telegram_id,
        )
        if raw:
            return [t.strip().upper() for t in raw.split(",") if t.strip()]

        return ["A", "B"]  # Fallback: all templates

    # ── Downline stats ─────────────────────────────────────────────────────────

    async def get_downline_stats(self, admin_id: int) -> dict:
        total_users = await self._pool.fetchval(
            "SELECT count(*) FROM users WHERE owner_admin_id = $1", admin_id
        )
        pending_deposits = await self._pool.fetchval(
            "SELECT count(*) FROM transactions "
            "WHERE admin_id = $1 AND kind = 'deposit' AND status = 'pending'",
            admin_id,
        )
        total_collected = await self._pool.fetchval(
            "SELECT coalesce(sum(amount), 0) FROM transactions "
            "WHERE admin_id = $1 AND kind = 'deposit' AND status = 'approved'",
            admin_id,
        )
        return {
            "total_users": int(total_users or 0),
            "pending_deposits": int(pending_deposits or 0),
            "total_collected": int(total_collected or 0),
        }

    async def list_downline_users(self, admin_id: int, limit: int = 200) -> list[dict]:
        """List the end-users bound to this reseller (owner_admin_id = admin_id).

        Free/VIP users sort first (an active role='vip' row in admins, or a
        price_override of 0), then most-recent. Each dict carries enough to
        render a list row and bucket the user without a second query:
          telegram_id, name, balance, price_override, vip_role, vip_active.
        The authoritative *true* total is get_downline_stats()['total_users'];
        this result is capped at `limit` for display.
        """
        rows = await self._pool.fetch(
            """
            SELECT u.telegram_id, u.name, u.balance, u.price_override,
                   a.role AS vip_role, a.active AS vip_active
              FROM users u
              LEFT JOIN admins a ON a.telegram_id = u.telegram_id
             WHERE u.owner_admin_id = $1
             ORDER BY (CASE WHEN u.price_override = 0 OR a.role = 'vip' THEN 0 ELSE 1 END),
                      u.created_at DESC
             LIMIT $2
            """,
            admin_id,
            int(limit),
        )
        return [dict(r) for r in rows]

    async def get_user_card(self, telegram_id: int) -> Optional[dict]:
        """Fetch one user's panel card for the 'My Users' detail view:
        telegram_id, name, balance, price_override, owner_admin_id, and is_vip
        (an active role='vip' row). Returns None if there is no users row."""
        row = await self._pool.fetchrow(
            """
            SELECT u.telegram_id, u.name, u.balance, u.price_override, u.owner_admin_id,
                   COALESCE(a.role = 'vip' AND a.active, FALSE) AS is_vip
              FROM users u
              LEFT JOIN admins a ON a.telegram_id = u.telegram_id
             WHERE u.telegram_id = $1
            """,
            telegram_id,
        )
        return dict(row) if row is not None else None

    # ── Users ─────────────────────────────────────────────────────────────────

    async def ensure_user(self, telegram_id: int, name: str = "") -> None:
        """Create the user if missing (idempotent). Safe to call on every touch.

        New users start at FREE_SIGNUP_BALANCE (set only on insert); existing
        users keep their balance and only have name/updated_at refreshed. The
        balance is never reset by this call.
        """
        await self._pool.execute(
            """
            INSERT INTO users (telegram_id, balance, name)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id)
            DO UPDATE SET name = EXCLUDED.name, updated_at = now()
            """,
            telegram_id,
            int(settings.FREE_SIGNUP_BALANCE),
            name or "",
        )

    async def get_balance(self, telegram_id: int) -> int:
        bal = await self._pool.fetchval(
            "SELECT balance FROM users WHERE telegram_id = $1", telegram_id
        )
        return int(bal) if bal is not None else 0

    # ── Charging (authoritative spend gate) ───────────────────────────────────

    async def charge(self, telegram_id: int, amount: int, note: str = "") -> bool:
        """Atomically debit `amount` Birr if the balance covers it.

        Returns True and records a 'debit' transaction when charged, False when
        the balance is insufficient. amount <= 0 is a free no-op (returns True).

        The `balance >= amount` filter + atomic decrement guarantee the balance
        never goes negative even under concurrent charges (row lock + re-check).
        """
        amount = int(amount)
        if amount <= 0:
            return True

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE users SET balance = balance - $2, updated_at = now()
                     WHERE telegram_id = $1 AND balance >= $2
                    RETURNING balance
                    """,
                    telegram_id,
                    amount,
                )
                if row is None:
                    # Insufficient balance (or unknown user) — nothing changed.
                    return False

                await conn.execute(
                    """
                    INSERT INTO transactions (telegram_id, kind, amount, status, note)
                    VALUES ($1, 'debit', $2, 'done', $3)
                    """,
                    telegram_id,
                    amount,
                    note,
                )
        return True

    # ── Deposits (admin-approved top-ups) ─────────────────────────────────────

    async def create_pending_deposit(
        self,
        telegram_id: int,
        amount: int,
        method: str,
        receipt_file_id: str,
        name: str = "",
        admin_id: Optional[int] = None,
    ) -> str:
        """Record a pending deposit and return its id (a UUID string) used in the
        approve/reject button callbacks."""
        # amount arrives as a float; asyncpg will NOT coerce float→BIGINT.
        amount = int(round(amount))
        # Guarantee the user row exists so approve's balance credit always hits.
        await self.ensure_user(telegram_id, name)
        new_id = await self._pool.fetchval(
            """
            INSERT INTO transactions
                (telegram_id, kind, amount, status, method, receipt_file_id, admin_id)
            VALUES ($1, 'deposit', $2, 'pending', $3, $4, $5)
            RETURNING id
            """,
            telegram_id,
            amount,
            method,
            receipt_file_id,
            admin_id,
        )
        return str(new_id)

    async def _decide_deposit(
        self,
        deposit_id: str,
        admin_id: int,
        approve: bool,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        """Shared idempotent approve/reject. Returns (telegram_id, amount) or None.

        When require_owner is set, the atomic filter also requires the deposit's
        admin_id to match it (owner-gating). Idempotent: a double-tap re-evaluates
        status='pending' on the committed row → 0 rows → None (no double credit).
        """
        oid = self._parse_deposit_id(deposit_id)
        if oid is None:
            return None

        new_status = "approved" if approve else "rejected"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE transactions
                       SET status = $2, decided_at = now(), decided_by = $3
                     WHERE id = $1 AND kind = 'deposit' AND status = 'pending'
                       AND ($4::bigint IS NULL OR admin_id = $4)
                    RETURNING telegram_id, amount
                    """,
                    oid,
                    new_status,
                    admin_id,
                    require_owner,
                )
                if row is None:
                    # Unknown deposit, not pending, or admin_id != require_owner.
                    return None

                telegram_id, amount = int(row["telegram_id"]), int(row["amount"])
                if approve:
                    # Credit in the SAME transaction (closes a crash window).
                    await conn.execute(
                        "UPDATE users SET balance = balance + $2, updated_at = now() "
                        "WHERE telegram_id = $1",
                        telegram_id,
                        amount,
                    )
        return (telegram_id, amount)

    async def decide_deposit(
        self,
        deposit_id: str,
        actor_id: int,
        approve: bool,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        return await self._decide_deposit(
            deposit_id, actor_id, approve=approve, require_owner=require_owner
        )

    async def approve_deposit(
        self,
        deposit_id: str,
        admin_id: int,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        return await self._decide_deposit(
            deposit_id, admin_id, approve=True, require_owner=require_owner
        )

    async def reject_deposit(
        self,
        deposit_id: str,
        admin_id: int,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        return await self._decide_deposit(
            deposit_id, admin_id, approve=False, require_owner=require_owner
        )
