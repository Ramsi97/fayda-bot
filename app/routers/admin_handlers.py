# app/routers/admin_handlers.py
"""
Admin self-service + super-admin management router.

Handles:
- Reseller settings: edit CBE / Telebirr info, change price, view referral link & stats.
- Super admin tools: add new reseller, soft remove reseller, list all admins.
"""

from typing import Optional
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.state import AdminStates

router = Router()


# ── Keyboards ──────────────────────────────────────────────────────────────────

def kb_admin_menu(is_super_admin: bool = False) -> types.InlineKeyboardMarkup:
    buttons = [
        [
            types.InlineKeyboardButton(text="🏦 Set CBE Account", callback_data="adm_set_cbe"),
            types.InlineKeyboardButton(text="📱 Set Telebirr", callback_data="adm_set_telebirr"),
        ],
        [
            types.InlineKeyboardButton(text="💵 Set Price", callback_data="adm_set_price"),
            types.InlineKeyboardButton(text="🔗 Referral Link", callback_data="adm_link"),
        ],
        [
            types.InlineKeyboardButton(text="📊 Downline Stats", callback_data="adm_stats"),
        ],
    ]
    if is_super_admin:
        buttons.append([
            types.InlineKeyboardButton(text="➕ Add Admin", callback_data="adm_add"),
            types.InlineKeyboardButton(text="👥 List Admins", callback_data="adm_list"),
        ])
    buttons.append([types.InlineKeyboardButton(text="🔙 Close Menu", callback_data="adm_close")])
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_admin_cancel() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="❌ Cancel", callback_data="adm_cancel_input")]]
    )


# ── Admin Menu Entrypoint ──────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(
    message: types.Message,
    state: FSMContext,
    role: str = "user",
):
    """Show admin panel for admins and super admins."""
    actor = message.from_user.id
    is_super = role == "super_admin" or actor in settings.super_admins
    if not is_super and role != "admin":
        return

    await state.clear()
    await message.answer(
        "🛠 *Admin Dashboard*\n\nSelect an option below to configure your reseller settings:",
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "adm_close")
async def cb_close_admin(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()


@router.callback_query(F.data == "adm_cancel_input")
async def cb_cancel_input(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    await state.clear()
    actor = callback.from_user.id
    is_super = role == "super_admin" or actor in settings.super_admins
    await callback.message.edit_text(
        "🛠 *Admin Dashboard*\n\nAction canceled. Select an option below:",
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
    )
    await callback.answer()


# ── Referral Link & Stats ──────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_link")
async def cb_admin_link(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    actor = callback.from_user.id
    if role not in ("super_admin", "admin") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    admin_doc = await billing.get_admin(actor)
    ref_code = admin_doc.get("ref_code", str(actor)) if admin_doc else str(actor)
    bot_username = callback.bot.username or callback.message.bot.username or ""
    link = f"https://t.me/{bot_username}?start={ref_code}"

    text = (
        "🔗 *Your Referral Link*\n\n"
        f"`{link}`\n\n"
        "Share this link with your customers. Any new user who starts the bot using this link will be bound to your reseller account!"
    )
    is_super = role == "super_admin" or actor in settings.super_admins
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "adm_stats")
async def cb_admin_stats(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    actor = callback.from_user.id
    if role not in ("super_admin", "admin") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    stats = await billing.get_downline_stats(actor)
    admin_doc = await billing.get_admin(actor)
    price = admin_doc.get("price_per_download", settings.PRICE_PER_DOWNLOAD) if admin_doc else settings.PRICE_PER_DOWNLOAD

    text = (
        "📊 *Your Reseller Performance*\n\n"
        f"👥 *Total Bound Users:* {stats['total_users']}\n"
        f"⏳ *Pending Deposits:* {stats['pending_deposits']}\n"
        f"💰 *Total Deposited:* {stats['total_collected']} Birr\n"
        f"💵 *Your Price Per Download:* {price} Birr\n"
    )
    is_super = role == "super_admin" or actor in settings.super_admins
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
    )
    await callback.answer()


# ── CBE Account Setup ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_set_cbe")
async def cb_set_cbe(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    actor = callback.from_user.id
    if role not in ("super_admin", "admin") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    await state.set_state(AdminStates.waiting_cbe_name)
    await callback.message.edit_text(
        "🏦 *Set CBE Details*\n\nStep 1/2: Please send the *Account Holder Name* for CBE:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_cbe_name, F.text)
async def state_cbe_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(cbe_name=name)
    await state.set_state(AdminStates.waiting_cbe_number)
    await message.answer(
        f"🏦 *Set CBE Details*\n\nStep 2/2: Account Name: `{name}`\n\nPlease send the *Account Number*:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )


@router.message(AdminStates.waiting_cbe_number, F.text)
async def state_cbe_number(
    message: types.Message,
    state: FSMContext,
    billing,
    role: str = "user",
):
    number = message.text.strip()
    data = await state.get_data()
    name = data.get("cbe_name", "")
    await billing.set_payment_fields(message.from_user.id, cbe_name=name, cbe_number=number)
    await state.clear()

    is_super = role == "super_admin" or message.from_user.id in settings.super_admins
    await message.answer(
        f"✅ *CBE Account Updated!*\n\n• Name: `{name}`\n• Number: `{number}`",
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
    )


# ── Telebirr Setup ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_set_telebirr")
async def cb_set_telebirr(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    actor = callback.from_user.id
    if role not in ("super_admin", "admin") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    await state.set_state(AdminStates.waiting_telebirr_name)
    await callback.message.edit_text(
        "📱 *Set Telebirr Details*\n\nStep 1/2: Please send the *Account Holder Name* for Telebirr:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_telebirr_name, F.text)
async def state_telebirr_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(telebirr_name=name)
    await state.set_state(AdminStates.waiting_telebirr_number)
    await message.answer(
        f"📱 *Set Telebirr Details*\n\nStep 2/2: Account Name: `{name}`\n\nPlease send the *Phone Number* (e.g. `0912345678`):",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )


@router.message(AdminStates.waiting_telebirr_number, F.text)
async def state_telebirr_number(
    message: types.Message,
    state: FSMContext,
    billing,
    role: str = "user",
):
    number = message.text.strip()
    data = await state.get_data()
    name = data.get("telebirr_name", "")
    await billing.set_payment_fields(message.from_user.id, telebirr_name=name, telebirr_number=number)
    await state.clear()

    is_super = role == "super_admin" or message.from_user.id in settings.super_admins
    await message.answer(
        f"✅ *Telebirr Account Updated!*\n\n• Name: `{name}`\n• Phone: `{number}`",
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
    )


# ── Price Setup ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_set_price")
async def cb_set_price(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    actor = callback.from_user.id
    if role not in ("super_admin", "admin") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    await state.set_state(AdminStates.waiting_price)
    await callback.message.edit_text(
        "💵 *Set Download Price*\n\nPlease send the price per download in Birr (e.g. `15`):",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_price, F.text)
async def state_price(
    message: types.Message,
    state: FSMContext,
    billing,
    role: str = "user",
):
    try:
        price = int(message.text.strip())
        if price < 0:
            raise ValueError
    except ValueError:
        return await message.answer(
            "⚠️ Invalid price. Please enter a positive integer:",
            reply_markup=kb_admin_cancel(),
        )

    await billing.set_price(message.from_user.id, price)
    await state.clear()

    is_super = role == "super_admin" or message.from_user.id in settings.super_admins
    await message.answer(
        f"✅ *Price Updated!*\n\nYour users will now be charged `{price}` Birr per card.",
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
    )


# ── Super Admin: Add Admin ────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_add")
async def cb_super_add_admin(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    actor = callback.from_user.id
    if role != "super_admin" and actor not in settings.super_admins:
        return await callback.answer("⛔ Super admin only.", show_alert=True)

    await state.set_state(AdminStates.waiting_add_admin_id)
    await callback.message.edit_text(
        "➕ *Add New Reseller Admin*\n\nSend the Telegram User ID (numeric) or forward a message from the user:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_add_admin_id)
async def state_add_admin(
    message: types.Message,
    state: FSMContext,
    billing,
    role: str = "user",
):
    target_id: Optional[int] = None

    if message.forward_from:
        target_id = message.forward_from.id
    elif message.text:
        try:
            target_id = int(message.text.strip())
        except ValueError:
            pass

    if not target_id:
        return await message.answer(
            "⚠️ Invalid ID. Please send a valid numeric Telegram ID or forward a message from the user:",
            reply_markup=kb_admin_cancel(),
        )

    await billing.upsert_admin(target_id, role="admin", active=True)
    await state.clear()

    is_super = role == "super_admin" or message.from_user.id in settings.super_admins
    bot_username = message.bot.username or ""
    await message.answer(
        f"✅ *Reseller Added!*\n\n"
        f"• User ID: `{target_id}`\n"
        f"• Role: `admin`\n"
        f"• Referral link: `https://t.me/{bot_username}?start={target_id}`\n\n"
        "They can now type /admin to configure payment accounts and price.",
        reply_markup=kb_admin_menu(is_super_admin=is_super),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# ── Super Admin: List Admins & Remove Admin ───────────────────────────────────

@router.callback_query(F.data == "adm_list")
async def cb_super_list_admins(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    actor = callback.from_user.id
    if role != "super_admin" and actor not in settings.super_admins:
        return await callback.answer("⛔ Super admin only.", show_alert=True)

    admins = await billing.list_admins()
    if not admins:
        return await callback.answer("No admins registered yet.", show_alert=True)

    text = "👥 *Registered Admins:*\n\n"
    keyboard = []

    for a in admins:
        aid = a["_id"]
        arole = a.get("role", "admin")
        active = a.get("active", True)
        status_str = "🟢 Active" if active else "🔴 Inactive"
        price = a.get("price_per_download", settings.PRICE_PER_DOWNLOAD)
        text += f"• `{aid}` ({arole}) — {status_str} — {price} Birr\n"

        if arole != "super_admin" and active:
            keyboard.append([
                types.InlineKeyboardButton(
                    text=f"🗑 Remove {aid}",
                    callback_data=f"adm_remove:{aid}",
                )
            ])

    keyboard.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="adm_cancel_input")])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_remove:"))
async def cb_super_remove_admin(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    actor = callback.from_user.id
    if role != "super_admin" and actor not in settings.super_admins:
        return await callback.answer("⛔ Super admin only.", show_alert=True)

    parts = callback.data.split(":", 1)
    if len(parts) != 2:
        return await callback.answer("Bad request.", show_alert=True)

    try:
        target_id = int(parts[1])
    except ValueError:
        return await callback.answer("Invalid ID.", show_alert=True)

    if target_id in settings.super_admins:
        return await callback.answer("Cannot remove a super admin.", show_alert=True)

    await billing.remove_admin(target_id)
    await callback.answer(f"Admin {target_id} deactivated.", show_alert=True)

    # Refresh list
    await cb_super_list_admins(callback, billing, role=role)
