"""
Integration check for the Postgres-backed BillingService (isolated schema).

Exercises the full billing contract against a real Postgres (e.g. Supabase)
using a dedicated throwaway schema (billing_test), then drops it. If no DB is
reachable it prints PG_UNREACHABLE and exits 0 (soft skip). Scratch file — safe
to delete.

Usage:
  DATABASE_URL=postgresql://... /path/to/python test_billing_pg.py
"""
import os, sys, asyncio

# Settings read env at import time — set required + test knobs first.
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("WEBHOOK_URL", "https://x")
os.environ["FREE_SIGNUP_BALANCE"] = "100"      # test the signup bonus path

import asyncpg
from app.config import settings
from services.billing_service import BillingService

TEST_SCHEMA = "billing_test"
PASS, FAIL = "OK ", "XX "
fails = 0


def check(label, cond):
    global fails
    print(f"{PASS if cond else FAIL} {label}")
    if not cond:
        fails += 1


async def main():
    b = BillingService(settings.DATABASE_URL, schema=TEST_SCHEMA)
    try:
        await b.init_db()
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
        # OSError covers ConnectionRefused / DNS (gaierror); the asyncpg classes
        # cover auth failures and server-side errors. Soft-skip when DB is down.
        print("PG_UNREACHABLE:", type(e).__name__, str(e)[:160])
        return 0

    # Clean slate for a deterministic run (tables live in the isolated schema).
    await b._pool.execute("TRUNCATE users, transactions")

    UID = 424242

    # New user gets FREE_SIGNUP_BALANCE.
    await b.ensure_user(UID, "Alice")
    check("new user seeded with FREE_SIGNUP_BALANCE (100)", await b.get_balance(UID) == 100)

    # Charge within balance.
    check("charge 30 succeeds", await b.charge(UID, 30, "card") is True)
    check("balance now 70", await b.get_balance(UID) == 70)

    # Charge beyond balance is refused and does not change balance.
    check("charge 1000 refused", await b.charge(UID, 1000, "card") is False)
    check("balance still 70", await b.get_balance(UID) == 70)

    # amount <= 0 is a free no-op.
    check("charge 0 is free True", await b.charge(UID, 0, "noop") is True)
    check("balance still 70 after 0-charge", await b.get_balance(UID) == 70)

    # ensure_user must NOT reset an existing balance; name refreshed.
    await b.ensure_user(UID, "Alice Renamed")
    check("ensure_user keeps balance (70)", await b.get_balance(UID) == 70)
    name = await b._pool.fetchval("SELECT name FROM users WHERE telegram_id = $1", UID)
    check("ensure_user refreshed name", name == "Alice Renamed")

    # Deposit -> approve credits exactly once (idempotent on double-tap).
    dep = await b.create_pending_deposit(UID, 50, "CBE", "file123", "Alice")
    check("create_pending_deposit returns 36-char uuid str", isinstance(dep, str) and len(dep) == 36)
    r1 = await b.approve_deposit(dep, 999)
    check("approve returns (uid, amount)", r1 == (UID, 50))
    check("balance credited to 120", await b.get_balance(UID) == 120)
    r2 = await b.approve_deposit(dep, 999)
    check("second approve is no-op (None)", r2 is None)
    check("balance unchanged after double-approve (120)", await b.get_balance(UID) == 120)

    # Reject a fresh deposit: returns (uid, amount) but does NOT credit.
    dep2 = await b.create_pending_deposit(UID, 70, "Telebirr", "file456", "Alice")
    r3 = await b.reject_deposit(dep2, 999)
    check("reject returns (uid, amount)", r3 == (UID, 70))
    check("reject does not credit (still 120)", await b.get_balance(UID) == 120)
    r4 = await b.reject_deposit(dep2, 999)
    check("second reject is no-op (None)", r4 is None)

    # Approving an already-rejected deposit must be refused.
    check("approve after reject is None", await b.approve_deposit(dep2, 999) is None)
    check("balance still 120", await b.get_balance(UID) == 120)

    # Malformed / unknown deposit ids are safe.
    check("bad uuid -> None", await b.approve_deposit("not-a-uuid", 999) is None)
    check("unknown uuid -> None", await b.approve_deposit("00000000-0000-0000-0000-000000000000", 999) is None)

    # get_balance for unknown user is 0.
    check("unknown user balance is 0", await b.get_balance(1) == 0)

    # Transaction ledger: 1 debit (30) + 2 deposits (approved/rejected).
    n_debit = await b._pool.fetchval(
        "SELECT count(*) FROM transactions WHERE telegram_id = $1 AND kind = 'debit'", UID)
    n_dep = await b._pool.fetchval(
        "SELECT count(*) FROM transactions WHERE telegram_id = $1 AND kind = 'deposit'", UID)
    check("one debit recorded", n_debit == 1)
    check("two deposits recorded", n_dep == 2)

    # ── Phase 0 ownership tests ──
    ADMIN_A = 7001
    ADMIN_B = 7002
    SUPER_ADMIN = 7790405623
    USER_C = 8001

    await b.upsert_admin(ADMIN_A, role="admin", price=15)
    await b.upsert_admin(ADMIN_B, role="admin", price=20)
    check("bind user to admin A succeeds", await b.bind_user_owner(USER_C, ADMIN_A) is True)
    check("second bind is ignored", await b.bind_user_owner(USER_C, ADMIN_B) is False)
    check("admin cannot be bound", await b.bind_user_owner(ADMIN_A, ADMIN_B) is False)
    check("get_owner returns ADMIN_A", await b.get_owner(USER_C) == ADMIN_A)
    check("USER_C price matches ADMIN_A price (15)", await b.get_price_for_user(USER_C) == 15)
    check("ADMIN_B configured price is 20", (await b.get_admin(ADMIN_B))["price_per_download"] == 20)
    check("ADMIN_B download price for self is free (0)", await b.get_price_for_user(ADMIN_B) == 0)

    # Deposit with admin_id: owner approves OK, foreign admin gets None, super admin override works
    dep_own = await b.create_pending_deposit(USER_C, 100, "CBE", "rec_c", "User C", admin_id=ADMIN_A)
    # Foreign admin (ADMIN_B) attempts approval with require_owner=ADMIN_B -> None
    r_foreign = await b.approve_deposit(dep_own, ADMIN_B, require_owner=ADMIN_B)
    check("foreign admin cannot approve (None)", r_foreign is None)
    check("USER_C balance unchanged after foreign attempt", await b.get_balance(USER_C) == 100)  # seeded with 100 signup balance

    # Owner (ADMIN_A) approves with require_owner=ADMIN_A -> OK
    r_owner = await b.approve_deposit(dep_own, ADMIN_A, require_owner=ADMIN_A)
    check("owner approves OK", r_owner == (USER_C, 100))
    check("USER_C balance credited to 200", await b.get_balance(USER_C) == 200)

    # Super admin override without require_owner
    dep_super = await b.create_pending_deposit(USER_C, 50, "Telebirr", "rec_s", "User C", admin_id=ADMIN_A)
    r_super = await b.approve_deposit(dep_super, SUPER_ADMIN, require_owner=None)
    check("super admin overrides without require_owner", r_super == (USER_C, 50))
    check("USER_C balance credited to 250", await b.get_balance(USER_C) == 250)

    # Reseller payment field updates & stats check
    await b.set_payment_fields(ADMIN_A, cbe_name="Admin CBE", cbe_number="1000999")
    a_doc = await b.get_admin(ADMIN_A)
    check("payment fields updated", a_doc["cbe"]["name"] == "Admin CBE" and a_doc["cbe"]["number"] == "1000999")
    check("get_admin exposes _id alias", a_doc["_id"] == ADMIN_A)
    stats = await b.get_downline_stats(ADMIN_A)
    check("downline stats computed", stats["total_users"] == 1 and stats["total_collected"] >= 100)

    # ── Per-user price override ──
    # USER_C is bound to ADMIN_A (price 15). An override wins over the owner price.
    check("no override by default", await b.get_user_price_override(USER_C) is None)
    check("set USER_C override to 5 (owner A)", await b.set_user_price(USER_C, 5, require_owner=ADMIN_A) is True)
    check("override reads back as 5", await b.get_user_price_override(USER_C) == 5)
    check("USER_C price now 5 (override beats owner 15)", await b.get_price_for_user(USER_C) == 5)
    # A foreign admin cannot change another admin's user.
    check("foreign admin cannot set override", await b.set_user_price(USER_C, 99, require_owner=ADMIN_B) is False)
    check("override unchanged at 5 after foreign attempt", await b.get_user_price_override(USER_C) == 5)
    # 0 is a valid override meaning "free for this user" (distinct from unset).
    check("set USER_C override to 0 (free)", await b.set_user_price(USER_C, 0, require_owner=ADMIN_A) is True)
    check("USER_C price now 0 (free)", await b.get_price_for_user(USER_C) == 0)
    # Clearing the override (None) reverts to the owner's price.
    check("clear USER_C override (super, no owner gate)", await b.set_user_price(USER_C, None, require_owner=None) is True)
    check("override now None again", await b.get_user_price_override(USER_C) is None)
    check("USER_C price reverts to owner A (15)", await b.get_price_for_user(USER_C) == 15)
    # Setting a price for a non-existent user updates nothing.
    check("override on unknown user is a no-op (False)", await b.set_user_price(999999, 5, require_owner=None) is False)

    # ── Role promotion on re-add (upsert_admin conflict) ──
    VIP_P, AGENT_P, SUPER_P = 7101, 7102, 7103
    # VIP (role vip, ref_code NULL, price 0) promoted to a reseller admin
    await b.upsert_vip(VIP_P)
    check("seeded VIP has role vip", await b.get_role(VIP_P) == "vip")
    await b.upsert_admin(VIP_P, role="admin")
    check("VIP promoted to admin", await b.get_role(VIP_P) == "admin")
    vip_row = await b.get_admin(VIP_P)
    check("promoted VIP got a ref_code (not NULL)", vip_row["ref_code"] == str(VIP_P))
    check("promoted VIP price reset to default (not 0)",
          vip_row["price_per_download"] == settings.PRICE_PER_DOWNLOAD and vip_row["price_per_download"] != 0)

    # Agent promoted to a reseller admin
    await b.upsert_agent(AGENT_P)
    check("seeded agent has role agent", await b.get_role(AGENT_P) == "agent")
    await b.upsert_admin(AGENT_P, role="admin")
    check("agent promoted to admin", await b.get_role(AGENT_P) == "admin")

    # Re-adding an existing admin preserves their configured price (no clobber)
    await b.upsert_admin(ADMIN_A, role="admin")   # no price arg -> would default to 10
    check("existing admin keeps configured price 15 on re-add",
          (await b.get_admin(ADMIN_A))["price_per_download"] == 15)

    # A super_admin is never demoted to admin by upsert_admin
    await b._pool.execute(
        "INSERT INTO admins (telegram_id, role, ref_code, price_per_download, active) "
        "VALUES ($1, 'super_admin', $2, 0, TRUE)",
        SUPER_P, str(SUPER_P),
    )
    await b.upsert_admin(SUPER_P, role="admin")
    check("super_admin not demoted by upsert_admin",
          (await b.get_admin(SUPER_P))["role"] == "super_admin")

    # Soft removal check
    check("soft remove admin", await b.remove_admin(ADMIN_B) is True)
    check("removed admin role is user", await b.get_role(ADMIN_B) == "user")

    # ── My Users: downline listing + per-user card ──
    # Build a small downline under ADMIN_A to exercise the "My Users" panel:
    # a free user (override 0), a system VIP (role='vip'), and a plain payer,
    # alongside USER_C (bound earlier, paying). A user owned by a *different*
    # admin must never leak into ADMIN_A's list.
    FREE_U, VIP_U, PAY_U, FOREIGN_U = 8002, 8003, 8004, 8005

    await b.bind_user_owner(FREE_U, ADMIN_A)
    await b.ensure_user(FREE_U, "Freebie")
    check("set FREE_U override to 0 (owner A)", await b.set_user_price(FREE_U, 0, require_owner=ADMIN_A) is True)

    await b.bind_user_owner(PAY_U, ADMIN_A)
    await b.ensure_user(PAY_U, "Payer")

    await b.bind_user_owner(VIP_U, ADMIN_A)
    await b.ensure_user(VIP_U, "Veep")
    await b.upsert_vip(VIP_U)                      # also give them an active role='vip' row
    check("VIP_U role is vip", await b.get_role(VIP_U) == "vip")

    # A user owned by ADMIN_B — must be excluded from ADMIN_A's list.
    await b.bind_user_owner(FOREIGN_U, ADMIN_B)

    listed = await b.list_downline_users(ADMIN_A)
    ids = [u["telegram_id"] for u in listed]
    check("list includes ADMIN_A's users (C, free, pay, vip)",
          set(ids) >= {USER_C, FREE_U, PAY_U, VIP_U})
    check("foreign user (owned by ADMIN_B) excluded", FOREIGN_U not in ids)
    owners_ok = True
    for i in ids:
        if await b.get_owner(i) != ADMIN_A:
            owners_ok = False
            break
    check("every listed row is owned by ADMIN_A", owners_ok)

    def _is_free(u):
        return (u.get("vip_role") == "vip" and u.get("vip_active")) or u.get("price_override") == 0

    free_ids = {u["telegram_id"] for u in listed if _is_free(u)}
    check("free bucket is exactly {FREE_U, VIP_U}", free_ids == {FREE_U, VIP_U})
    check("free/VIP users sort before paying ones",
          all(_is_free(u) for u in listed[:len(free_ids)]))

    by_id = {u["telegram_id"]: u for u in listed}
    check("list row carries the refreshed name", by_id[PAY_U]["name"] == "Payer")

    # get_user_card: one-row panel card used by the detail view.
    card_vip = await b.get_user_card(VIP_U)
    check("card(VIP_U) reports is_vip True", card_vip is not None and card_vip["is_vip"] is True)
    check("card(VIP_U) balance matches get_balance", card_vip["balance"] == await b.get_balance(VIP_U))

    card_free = await b.get_user_card(FREE_U)
    check("card(FREE_U) override is 0", card_free["price_override"] == 0)
    check("card(FREE_U) is_vip False", card_free["is_vip"] is False)
    check("card(FREE_U) owner is ADMIN_A", card_free["owner_admin_id"] == ADMIN_A)
    check("card(unknown user) is None", await b.get_user_card(123123123) is None)

    # Re-running init_db is safe & idempotent
    await b.init_db()
    check("idempotent init_db completes cleanly", True)

    # Drop the throwaway schema and close.
    await b._pool.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
    await b.close()

    print(f"\n{'ALL GOOD' if fails == 0 else str(fails) + ' CHECK(S) FAILED'}")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
