"""
Billing / points service.

Gives every Telegram user a **Birr balance** that is debited per generated ID
card and topped up via admin-approved deposits. Backed by **MongoDB** (motor)
so balances survive restarts and redeploys — unlike a file on the container
filesystem, a managed MongoDB (e.g. MongoDB Atlas) is truly persistent and
multi-instance safe (see app/config.py: MONGODB_URI / MONGODB_DB and
AZURE_DEPLOYMENT.md).

Atomicity comes from MongoDB's single-document atomic operators rather than an
in-process lock, so this is correct even if several bot instances run at once:
  * charge  -> find_one_and_update with a conditional {balance >= amount} filter
               and an atomic $inc, so a balance can never go negative.
  * approve -> a status-gated find_one_and_update claims the pending deposit
               (making double-tap a no-op) before the balance is credited.

Collections
  users         _id = telegram_id (int); fields: name, balance (int Birr),
                created_at, updated_at.
  transactions  _id = ObjectId; fields: telegram_id, kind ('deposit'|'debit'),
                amount (int Birr), status ('pending'|'approved'|'rejected'|'done'),
                method, receipt_file_id, note, created_at, decided_at, decided_by.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings


class BillingService:
    def __init__(self, uri: str, db_name: str):
        self.uri = uri
        self.db_name = db_name
        self._client: Optional[AsyncIOMotorClient] = None
        self.users = None          # AsyncIOMotorCollection
        self.transactions = None   # AsyncIOMotorCollection
        self.admins = None        # AsyncIOMotorCollection

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def init_db(self) -> None:
        """Connect, verify the server is reachable, and ensure indexes.

        Called once at startup. Pings the server so a bad URI / unreachable
        cluster fails loudly here instead of on the first user action.
        """
        self._client = AsyncIOMotorClient(self.uri, serverSelectionTimeoutMS=5000)
        # Fail fast & loud if the cluster is unreachable or the URI is wrong.
        await self._client.admin.command("ping")
        db = self._client[self.db_name]
        self.users = db["users"]
        self.transactions = db["transactions"]
        self.admins = db["admins"]

        # Indexes
        await self.transactions.create_index("telegram_id")
        try:
            await self.admins.create_index("ref_code", unique=True)
        except DuplicateKeyError:
            pass

        try:
            await self.users.create_index("owner_admin_id")
        except DuplicateKeyError:
            pass

        try:
            await self.transactions.create_index("admin_id")
        except DuplicateKeyError:
            pass

        # Idempotent seed: upsert an admins doc per super admin
        super_ids = settings.super_admin_ids_ordered
        primary_super_admin = super_ids[0] if super_ids else None
        now = self._now()

        for tg_id in super_ids:
            await self.admins.update_one(
                {"_id": tg_id},
                {
                    "$setOnInsert": {
                        "role": "super_admin",
                        "ref_code": str(tg_id),
                        "price_per_download": int(settings.PRICE_PER_DOWNLOAD),
                        "active": True,
                        "cbe": {"name": "", "number": ""},
                        "telebirr": {"name": "", "number": ""},
                        "created_at": now,
                    },
                    "$set": {
                        "updated_at": now,
                    },
                },
                upsert=True,
            )

        # Backfill users missing owner_admin_id to primary super admin if available
        if primary_super_admin is not None:
            await self.users.update_many(
                {"owner_admin_id": {"$exists": False}},
                {"$set": {"owner_admin_id": primary_super_admin}},
            )
            await self.transactions.update_many(
                {"kind": "deposit", "status": "pending", "admin_id": {"$exists": False}},
                {"$set": {"admin_id": primary_super_admin}},
            )

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── Admin & Reseller Methods ──────────────────────────────────────────────

    async def get_admin(self, admin_id: int) -> Optional[dict]:
        return await self.admins.find_one({"_id": admin_id})

    async def get_admin_by_ref(self, ref_code: str) -> Optional[dict]:
        if not ref_code:
            return None
        return await self.admins.find_one({"ref_code": str(ref_code).strip()})

    async def upsert_admin(
        self,
        admin_id: int,
        role: str = "admin",
        price: Optional[int] = None,
        active: bool = True,
    ) -> dict:
        now = self._now()
        price_val = int(price) if price is not None else int(settings.PRICE_PER_DOWNLOAD)
        await self.admins.update_one(
            {"_id": admin_id},
            {
                "$setOnInsert": {
                    "role": role,
                    "ref_code": str(admin_id),
                    "price_per_download": price_val,
                    "active": active,
                    "cbe": {"name": "", "number": ""},
                    "telebirr": {"name": "", "number": ""},
                    "created_at": now,
                },
                "$set": {
                    "active": active,
                    "updated_at": now,
                },
            },
            upsert=True,
        )
        return await self.admins.find_one({"_id": admin_id})

    async def set_payment_fields(
        self,
        admin_id: int,
        cbe_name: Optional[str] = None,
        cbe_number: Optional[str] = None,
        telebirr_name: Optional[str] = None,
        telebirr_number: Optional[str] = None,
    ) -> bool:
        sets = {"updated_at": self._now()}
        if cbe_name is not None:
            sets["cbe.name"] = cbe_name
        if cbe_number is not None:
            sets["cbe.number"] = cbe_number
        if telebirr_name is not None:
            sets["telebirr.name"] = telebirr_name
        if telebirr_number is not None:
            sets["telebirr.number"] = telebirr_number
        res = await self.admins.update_one({"_id": admin_id}, {"$set": sets})
        return res.matched_count > 0

    async def set_price(self, admin_id: int, price: int) -> bool:
        res = await self.admins.update_one(
            {"_id": admin_id},
            {"$set": {"price_per_download": int(price), "updated_at": self._now()}},
        )
        return res.matched_count > 0

    async def list_admins(self) -> list[dict]:
        cursor = self.admins.find({})
        return await cursor.to_list(length=1000)

    async def remove_admin(self, admin_id: int) -> bool:
        """Soft remove admin by marking active=False."""
        res = await self.admins.update_one(
            {"_id": admin_id},
            {"$set": {"active": False, "updated_at": self._now()}},
        )
        return res.matched_count > 0

    async def bind_user_owner(self, telegram_id: int, admin_id: int) -> bool:
        """Atomic first-wins binding. Refuses if user is an admin or already bound."""
        # Check if the user is an admin
        admin_doc = await self.admins.find_one({"_id": telegram_id})
        if admin_doc is not None:
            return False

        now = self._now()
        # First attempt: update an existing unbound user doc
        res = await self.users.update_one(
            {"_id": telegram_id, "owner_admin_id": {"$exists": False}},
            {"$set": {"owner_admin_id": admin_id, "updated_at": now}},
        )
        if res.modified_count > 0:
            return True

        # If no existing doc was modified, check whether the user already existed
        # (i.e. owner_admin_id is already set — first-wins, refuse)
        existing = await self.users.find_one({"_id": telegram_id}, projection={"owner_admin_id": 1})
        if existing is not None:
            # User row exists but was not modified → already bound
            return False

        # User row doesn't exist at all — create it with owner_admin_id set
        try:
            await self.users.insert_one({
                "_id": telegram_id,
                "owner_admin_id": admin_id,
                "balance": int(settings.FREE_SIGNUP_BALANCE),
                "name": "",
                "created_at": now,
                "updated_at": now,
            })
            return True
        except Exception:
            # Race: another call inserted the row between our check and insert
            return False

    async def get_owner(self, telegram_id: int) -> Optional[int]:
        doc = await self.users.find_one({"_id": telegram_id}, projection={"owner_admin_id": 1})
        if doc and "owner_admin_id" in doc:
            return doc["owner_admin_id"]
        return None

    async def get_role(self, telegram_id: int) -> str:
        """Returns 'super_admin' | 'admin' | 'user'."""
        if telegram_id in settings.super_admins:
            return "super_admin"
        admin_doc = await self.admins.find_one({"_id": telegram_id})
        if admin_doc and admin_doc.get("active", True):
            if admin_doc.get("role") == "super_admin":
                return "super_admin"
            return "admin"
        return "user"

    async def get_price_for_user(self, telegram_id: int) -> int:
        """Returns admin doc price if user is an admin, else owner's price, else default settings price."""
        # 1. Admin doc price if user is an admin
        admin_doc = await self.admins.find_one({"_id": telegram_id})
        if admin_doc and admin_doc.get("price_per_download") is not None:
            return int(admin_doc["price_per_download"])

        # 2. Owner's price if user has an owner
        user_doc = await self.users.find_one({"_id": telegram_id}, projection={"owner_admin_id": 1})
        if user_doc and user_doc.get("owner_admin_id"):
            owner_doc = await self.admins.find_one({"_id": user_doc["owner_admin_id"]})
            if owner_doc and owner_doc.get("price_per_download") is not None:
                return int(owner_doc["price_per_download"])

        # 3. Fallback
        return int(settings.PRICE_PER_DOWNLOAD)

    async def get_downline_stats(self, admin_id: int) -> dict:
        total_users = await self.users.count_documents({"owner_admin_id": admin_id})
        pending_deposits = await self.transactions.count_documents({
            "admin_id": admin_id,
            "kind": "deposit",
            "status": "pending",
        })
        pipeline = [
            {"$match": {"admin_id": admin_id, "kind": "deposit", "status": "approved"}},
            {"$group": {"_id": None, "total_collected": {"$sum": "$amount"}}},
        ]
        agg_res = await self.transactions.aggregate(pipeline).to_list(length=1)
        total_collected = agg_res[0]["total_collected"] if agg_res else 0
        return {
            "total_users": total_users,
            "pending_deposits": pending_deposits,
            "total_collected": total_collected,
        }

    # ── Users ─────────────────────────────────────────────────────────────────

    async def ensure_user(self, telegram_id: int, name: str = "") -> None:
        """Create the user if missing (idempotent). Safe to call on every touch.

        New users start at FREE_SIGNUP_BALANCE ($setOnInsert); existing users
        keep their balance and only have name/updated_at refreshed. The balance
        is never reset by this call.
        """
        now = self._now()
        await self.users.update_one(
            {"_id": telegram_id},
            {
                "$setOnInsert": {
                    "balance": int(settings.FREE_SIGNUP_BALANCE),
                    "created_at": now,
                },
                "$set": {"name": name or "", "updated_at": now},
            },
            upsert=True,
        )

    async def get_balance(self, telegram_id: int) -> int:
        doc = await self.users.find_one(
            {"_id": telegram_id}, projection={"balance": 1}
        )
        return int(doc["balance"]) if doc else 0

    # ── Charging (authoritative spend gate) ───────────────────────────────────

    async def charge(self, telegram_id: int, amount: int, note: str = "") -> bool:
        """Atomically debit `amount` Birr if the balance covers it.

        Returns True and records a 'debit' transaction when charged, False when
        the balance is insufficient. amount <= 0 is a free no-op (returns True).

        The {balance >= amount} filter + atomic $inc guarantee the balance never
        goes negative even under concurrent charges.
        """
        amount = int(amount)
        if amount <= 0:
            return True

        now = self._now()
        doc = await self.users.find_one_and_update(
            {"_id": telegram_id, "balance": {"$gte": amount}},
            {"$inc": {"balance": -amount}, "$set": {"updated_at": now}},
        )
        if doc is None:
            # Insufficient balance (or unknown user) — nothing changed.
            return False

        await self.transactions.insert_one({
            "telegram_id": telegram_id,
            "kind": "debit",
            "amount": amount,
            "status": "done",
            "note": note,
            "created_at": now,
        })
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
        """Record a pending deposit and return its id (a 24-hex ObjectId string)
        used in the approve/reject button callbacks."""
        amount = int(round(amount))
        now = self._now()
        # Guarantee the user row exists so approve's balance $inc always hits.
        await self.ensure_user(telegram_id, name)
        tx_doc = {
            "telegram_id": telegram_id,
            "kind": "deposit",
            "amount": amount,
            "status": "pending",
            "method": method,
            "receipt_file_id": receipt_file_id,
            "created_at": now,
        }
        if admin_id is not None:
            tx_doc["admin_id"] = admin_id
        res = await self.transactions.insert_one(tx_doc)
        return str(res.inserted_id)

    async def _decide_deposit(
        self,
        deposit_id: str,
        admin_id: int,
        approve: bool,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        """Shared idempotent approve/reject. Returns (telegram_id, amount) or None.

        When require_owner is set, adds admin_id: require_owner to the atomic filter.
        """
        try:
            oid = ObjectId(deposit_id)
        except (InvalidId, TypeError):
            return None

        now = self._now()
        new_status = "approved" if approve else "rejected"
        filter_query = {"_id": oid, "kind": "deposit", "status": "pending"}
        if require_owner is not None:
            filter_query["admin_id"] = require_owner

        doc = await self.transactions.find_one_and_update(
            filter_query,
            {"$set": {"status": new_status, "decided_at": now, "decided_by": admin_id}},
        )
        if doc is None:
            # Unknown deposit, not pending, or admin_id didn't match require_owner
            return None

        telegram_id, amount = int(doc["telegram_id"]), int(doc["amount"])
        if approve:
            await self.users.update_one(
                {"_id": telegram_id},
                {"$inc": {"balance": amount}, "$set": {"updated_at": now}},
            )
        return (telegram_id, amount)

    async def decide_deposit(
        self,
        deposit_id: str,
        actor_id: int,
        approve: bool,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        return await self._decide_deposit(deposit_id, actor_id, approve=approve, require_owner=require_owner)

    async def approve_deposit(
        self,
        deposit_id: str,
        admin_id: int,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        return await self._decide_deposit(deposit_id, admin_id, approve=True, require_owner=require_owner)

    async def reject_deposit(
        self,
        deposit_id: str,
        admin_id: int,
        require_owner: Optional[int] = None,
    ) -> Optional[Tuple[int, int]]:
        return await self._decide_deposit(deposit_id, admin_id, approve=False, require_owner=require_owner)
