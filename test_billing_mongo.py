"""
Integration check for the MongoDB-backed BillingService (throwaway DB).

Exercises the full billing contract against a real MongoDB using a disposable
database (fayda_bot_test), then drops it. If no MongoDB is reachable it prints
MONGO_UNREACHABLE and exits 0 (soft skip). Scratch file — safe to delete.

Usage:
  MONGODB_URI=mongodb://localhost:27017 /path/to/python test_billing_mongo.py
"""
import os, sys, asyncio

# Settings read env at import time — set required + test knobs first.
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("WEBHOOK_URL", "https://x")
os.environ["FREE_SIGNUP_BALANCE"] = "100"      # test the signup bonus path
os.environ.setdefault("MONGODB_DB", "fayda_bot_test")

from pymongo.errors import ServerSelectionTimeoutError
from app.config import settings
from services.billing_service import BillingService

PASS, FAIL = "OK ", "XX "
fails = 0

def check(label, cond):
    global fails
    print(f"{PASS if cond else FAIL} {label}")
    if not cond:
        fails += 1

async def main():
    b = BillingService(settings.MONGODB_URI, settings.MONGODB_DB)
    try:
        await b.init_db()
    except ServerSelectionTimeoutError as e:
        print("MONGO_UNREACHABLE:", str(e)[:120])
        return 0

    # Clean slate for a deterministic run.
    await b.users.delete_many({})
    await b.transactions.delete_many({})

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
    doc = await b.users.find_one({"_id": UID})
    check("ensure_user refreshed name", doc["name"] == "Alice Renamed")

    # Deposit -> approve credits exactly once (idempotent on double-tap).
    dep = await b.create_pending_deposit(UID, 50, "CBE", "file123", "Alice")
    check("create_pending_deposit returns 24-hex str", isinstance(dep, str) and len(dep) == 24)
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
    check("bad ObjectId -> None", await b.approve_deposit("not-an-objectid", 999) is None)
    check("unknown ObjectId -> None", await b.approve_deposit("0" * 24, 999) is None)

    # get_balance for unknown user is 0.
    check("unknown user balance is 0", await b.get_balance(1) == 0)

    # Transaction ledger: 1 debit (30) + 2 deposits (approved/rejected).
    n_debit = await b.transactions.count_documents({"telegram_id": UID, "kind": "debit"})
    n_dep = await b.transactions.count_documents({"telegram_id": UID, "kind": "deposit"})
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
    check("ADMIN_B price is 20", await b.get_price_for_user(ADMIN_B) == 20)

    # Deposit with admin_id: owner approves OK, foreign admin gets None, super admin override works
    dep_own = await b.create_pending_deposit(USER_C, 100, "CBE", "rec_c", "User C", admin_id=ADMIN_A)
    # Foreign admin (ADMIN_B) attempts approval with require_owner=ADMIN_B -> None
    r_foreign = await b.approve_deposit(dep_own, ADMIN_B, require_owner=ADMIN_B)
    check("foreign admin cannot approve (None)", r_foreign is None)
    check("USER_C balance unchanged after foreign attempt", await b.get_balance(USER_C) == 100) # seeded with 100 signup balance

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
    stats = await b.get_downline_stats(ADMIN_A)
    check("downline stats computed", stats["total_users"] == 1 and stats["total_collected"] >= 100)

    # Soft removal check
    check("soft remove admin", await b.remove_admin(ADMIN_B) is True)
    check("removed admin role is user", await b.get_role(ADMIN_B) == "user")

    # Re-running init_db is safe & idempotent
    await b.init_db()
    check("idempotent init_db completes cleanly", True)

    # Drop the throwaway DB and close.
    await b._client.drop_database(settings.MONGODB_DB)
    await b.close()

    print(f"\n{'ALL GOOD' if fails == 0 else str(fails) + ' CHECK(S) FAILED'}")
    return 1 if fails else 0

sys.exit(asyncio.run(main()))
