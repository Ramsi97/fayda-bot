# app/routers/payment_handlers.py
"""
Payment / Deposit UI + admin approval flow (Amharic for users, English for admins).

Users deposit via CBE/Telebirr and upload a receipt; the bot records a pending
deposit (BillingService) and forwards the receipt to admins with Approve/Reject
inline buttons. Approving credits the user's balance.
"""

from typing import Optional
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.state import DepositStates
from utils.texts import (
    BALANCE_TEXT,
    BATCH_PARTIAL_BALANCE,  # noqa: F401  (kept for symmetry / future use)
    DEPOSIT_ADMIN_NOTE,
    DEPOSIT_APPROVED,
    DEPOSIT_ASK_RECEIPT,
    DEPOSIT_CBE_INFO,
    DEPOSIT_CHOOSE_METHOD,
    DEPOSIT_INVALID_AMOUNT,
    DEPOSIT_INVALID_RECEIPT,
    DEPOSIT_PAYMENT_NOT_SET,
    DEPOSIT_REJECTED,
    DEPOSIT_SUBMITTED,
    DEPOSIT_TELEBIRR_INFO,
)

router = Router()


def _admin_targets(owner_admin_id: Optional[int] = None) -> list[int]:
    """Chats to forward deposit receipts to. Prioritizes owner admin, falling back to super admins."""
    targets = []
    if owner_admin_id:
        targets.append(owner_admin_id)
    if settings.ADMIN_CHAT_ID and settings.ADMIN_CHAT_ID not in targets:
        targets.append(settings.ADMIN_CHAT_ID)
    for sa in settings.super_admins:
        if sa not in targets:
            targets.append(sa)
    return targets


# ── Keyboards ──────────────────────────────────────────────────────────────────

def kb_deposit_decision(deposit_id: str) -> types.InlineKeyboardMarkup:
    """Approve / Reject inline buttons shown to admins under a forwarded receipt."""
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(text="✅ Approve", callback_data=f"dep_approve:{deposit_id}"),
            types.InlineKeyboardButton(text="❌ Reject", callback_data=f"dep_reject:{deposit_id}"),
        ]]
    )


def kb_deposit_methods():
    """Payment method selection keyboard."""
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [
                types.KeyboardButton(text="🏦 CBE"),
                types.KeyboardButton(text="📱 ቴሌብር"),
            ],
            [types.KeyboardButton(text="🔙 ዋና ምናሌ")],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def kb_cancel_deposit():
    """Cancel keyboard shown during deposit flow."""
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="❌ ሰርዝ")]],
        resize_keyboard=True,
        persistent=True,
    )


def kb_back_to_main():
    """Minimal keyboard to go back to the main menu."""
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 ዋና ምናሌ")]],
        resize_keyboard=True,
        persistent=True,
    )


# ── Cancel / Back (deposit-specific) ──────────────────────────────────────────

@router.message(F.text == "❌ ሰርዝ")
@router.message(F.text == "🔙 ዋና ምናሌ")
async def deposit_cancel(message: types.Message, state: FSMContext):
    """Cancel the deposit flow and go back to main menu."""
    await state.clear()
    # Import here to avoid circular imports; the main kb lives in bot_handlers
    from app.routers.bot_handlers import kb_main
    await message.answer("🔙 ወደ ዋና ምናሌ ተመልሰዋል።", reply_markup=kb_main())


# ── Balance ───────────────────────────────────────────────────────────────────

@router.message(F.text == "💰 ቀሪ ሒሳብ")
@router.message(Command("balance"))
async def show_balance(message: types.Message, billing):
    """Show the user's current balance and how many downloads it covers."""
    user = message.from_user
    name = user.full_name or user.username or "ተጠቃሚ"

    await billing.ensure_user(user.id, name)
    balance = await billing.get_balance(user.id)
    price = await billing.get_price_for_user(user.id)
    downloads = str(balance // price) if price > 0 else "∞"

    await message.answer(
        BALANCE_TEXT.format(name=name, balance=balance, downloads=downloads, price=price),
        parse_mode="Markdown",
    )


# ── Step 1: Start Deposit ────────────────────────────────────────────────────

@router.message(F.text == "💰 ገንዘብ ማስገባት")
@router.message(Command("deposit"))
async def start_deposit(message: types.Message, state: FSMContext):
    """Show payment method selection (CBE / Telebirr)."""
    await state.set_state(DepositStates.choosing_method)
    await message.answer(
        DEPOSIT_CHOOSE_METHOD,
        parse_mode="Markdown",
        reply_markup=kb_deposit_methods(),
    )


async def _resolve_payment_admin_doc(
    billing,
    admin_doc: Optional[dict],
    owner_admin_id: Optional[int],
) -> tuple[Optional[dict], Optional[int]]:
    """Resolve payment account document and receiving admin ID.

    If owner is an agent (or user has no owner / admin has no payment accounts set),
    route payment accounts and receipts to the primary super admin.
    """
    if admin_doc is None and owner_admin_id is not None:
        admin_doc = await billing.get_admin(owner_admin_id)

    # Agents cannot have payment accounts — route their users to super admin
    if (admin_doc and admin_doc.get("role") == "agent") or owner_admin_id is None:
        super_ids = settings.super_admin_ids_ordered
        primary_super = super_ids[0] if super_ids else None
        if primary_super:
            super_doc = await billing.get_admin(primary_super)
            return super_doc, primary_super

    return admin_doc, owner_admin_id


# ── Step 2: Choose Payment Method ────────────────────────────────────────────

@router.message(DepositStates.choosing_method, F.text == "🏦 CBE")
async def choose_cbe(
    message: types.Message,
    state: FSMContext,
    billing,
    admin_doc: Optional[dict] = None,
    owner_admin_id: Optional[int] = None,
):
    """User selected CBE — show account info and ask for amount."""
    admin_doc, _ = await _resolve_payment_admin_doc(billing, admin_doc, owner_admin_id)

    cbe_info = admin_doc.get("cbe", {}) if admin_doc else {}
    name = cbe_info.get("name", "").strip()
    number = cbe_info.get("number", "").strip()

    if not name or not number:
        return await message.answer(
            DEPOSIT_PAYMENT_NOT_SET,
            parse_mode="Markdown",
            reply_markup=kb_back_to_main(),
        )

    await state.update_data(method="🏦 CBE")
    await state.set_state(DepositStates.waiting_amount)
    await message.answer(
        DEPOSIT_CBE_INFO.format(account_name=name, account_number=number),
        parse_mode="Markdown",
        reply_markup=kb_cancel_deposit(),
    )


@router.message(DepositStates.choosing_method, F.text == "📱 ቴሌብር")
async def choose_telebirr(
    message: types.Message,
    state: FSMContext,
    billing,
    admin_doc: Optional[dict] = None,
    owner_admin_id: Optional[int] = None,
):
    """User selected Telebirr — show phone info and ask for amount."""
    admin_doc, _ = await _resolve_payment_admin_doc(billing, admin_doc, owner_admin_id)

    telebirr_info = admin_doc.get("telebirr", {}) if admin_doc else {}
    name = telebirr_info.get("name", "").strip()
    number = telebirr_info.get("number", "").strip()

    if not name or not number:
        return await message.answer(
            DEPOSIT_PAYMENT_NOT_SET,
            parse_mode="Markdown",
            reply_markup=kb_back_to_main(),
        )

    await state.update_data(method="📱 ቴሌብር")
    await state.set_state(DepositStates.waiting_amount)
    await message.answer(
        DEPOSIT_TELEBIRR_INFO.format(account_name=name, account_number=number),
        parse_mode="Markdown",
        reply_markup=kb_cancel_deposit(),
    )


# ── Step 3: Enter Amount ─────────────────────────────────────────────────────

@router.message(DepositStates.waiting_amount, F.text)
async def receive_amount(message: types.Message, state: FSMContext):
    """Validate the amount and ask for a receipt screenshot."""
    raw = message.text.strip().replace(",", "")

    try:
        amount = float(raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        return await message.answer(
            DEPOSIT_INVALID_AMOUNT,
            parse_mode="Markdown",
        )

    await state.update_data(amount=amount)
    await state.set_state(DepositStates.waiting_receipt)
    await message.answer(
        DEPOSIT_ASK_RECEIPT,
        parse_mode="Markdown",
        reply_markup=kb_cancel_deposit(),
    )


# ── Step 4: Upload Receipt Screenshot ────────────────────────────────────────

@router.message(DepositStates.waiting_receipt, F.photo)
async def receive_receipt(
    message: types.Message,
    state: FSMContext,
    billing,
    owner_admin_id: Optional[int] = None,
):
    """User sent a receipt photo — record a pending deposit and forward to owning admin."""
    photo = message.photo[-1]
    file_id = photo.file_id

    data = await state.get_data()
    method = data.get("method", "N/A")
    amount = data.get("amount", 0)

    user = message.from_user
    name = user.full_name or user.username or "ተጠቃሚ"

    # Route agent's downline deposits to the primary super admin
    _, target_admin_id = await _resolve_payment_admin_doc(billing, None, owner_admin_id)

    # Persist a PENDING deposit linked to target_admin_id
    deposit_id = await billing.create_pending_deposit(
        telegram_id=user.id,
        amount=amount,
        method=method,
        receipt_file_id=file_id,
        name=name,
        admin_id=target_admin_id,
    )

    # Forward the receipt to admin with Approve/Reject buttons
    caption = DEPOSIT_ADMIN_NOTE.format(
        name=name, user_id=user.id, amount=int(amount),
        method=method, deposit_id=deposit_id,
    )
    kb = kb_deposit_decision(deposit_id)
    notified = False

    # Attempt to notify owning admin first, fall back to super admins if needed
    for admin_id in _admin_targets(target_admin_id):
        try:
            await message.bot.send_photo(
                chat_id=admin_id, photo=file_id, caption=caption,
                reply_markup=kb, parse_mode="Markdown",
            )
            notified = True
            # If target admin was successfully notified in DM, we don't spam super admins
            if admin_id == target_admin_id:
                break
        except Exception as e:
            print(f"[deposit] failed to notify admin {admin_id}: {e}")

    if not notified:
        print(f"[deposit] WARNING: deposit #{deposit_id} created but no admin was notified.")

    await state.clear()

    from app.routers.bot_handlers import kb_main
    await message.answer(
        DEPOSIT_SUBMITTED.format(method=method, amount=int(amount)),
        parse_mode="Markdown",
        reply_markup=kb_main(),
    )


@router.message(DepositStates.waiting_receipt)
async def receipt_not_photo(message: types.Message):
    """User sent something other than a photo while we expected a receipt."""
    await message.answer(
        DEPOSIT_INVALID_RECEIPT,
        parse_mode="Markdown",
    )


# ── Admin: Approve / Reject deposit (inline buttons) ──────────────────────────

async def _finalize_decision(
    callback: types.CallbackQuery,
    billing,
    approve: bool,
    role: str = "user",
):
    """Shared handler for approve/reject. Idempotent + authorized-only."""
    actor = callback.from_user.id
    if role not in ("super_admin", "admin") and actor not in settings.super_admins:
        return await callback.answer("⛔ Not authorized.", show_alert=True)

    parts = callback.data.split(":", 1)
    if len(parts) != 2 or not parts[1]:
        return await callback.answer("Bad request.", show_alert=True)
    deposit_id = parts[1]

    # Super admins can approve any deposit; regular admins must own the deposit
    require_owner = actor if role == "admin" and actor not in settings.super_admins else None

    if approve:
        result = await billing.approve_deposit(deposit_id, actor, require_owner=require_owner)
    else:
        result = await billing.reject_deposit(deposit_id, actor, require_owner=require_owner)

    if result is None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return await callback.answer("Already handled or unauthorized.", show_alert=True)

    telegram_id, amount = result

    # Notify the user
    if approve:
        new_balance = await billing.get_balance(telegram_id)
        user_text = DEPOSIT_APPROVED.format(amount=amount, balance=new_balance)
        note = f"\n\n✅ Approved by {callback.from_user.full_name} (+{amount} Birr)."
        toast = "Approved ✅"
    else:
        user_text = DEPOSIT_REJECTED.format(amount=amount)
        note = f"\n\n❌ Rejected by {callback.from_user.full_name}."
        toast = "Rejected ❌"

    try:
        await callback.bot.send_message(telegram_id, user_text, parse_mode="Markdown")
    except Exception as e:
        print(f"[deposit] couldn't notify user {telegram_id}: {e}")

    # Annotate the admin message and remove buttons
    base = callback.message.caption or ""
    try:
        await callback.message.edit_caption(
            caption=base + note, reply_markup=None, parse_mode="Markdown",
        )
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

    await callback.answer(toast)


@router.callback_query(F.data.startswith("dep_approve:"))
async def cb_deposit_approve(callback: types.CallbackQuery, billing, role: str = "user"):
    await _finalize_decision(callback, billing, approve=True, role=role)


@router.callback_query(F.data.startswith("dep_reject:"))
async def cb_deposit_reject(callback: types.CallbackQuery, billing, role: str = "user"):
    await _finalize_decision(callback, billing, approve=False, role=role)

