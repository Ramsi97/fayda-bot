# app/routers/admin_handlers.py
"""
Admin self-service + super-admin management router.

Roles handled here:
- super_admin : full control (CBE/Telebirr, price, user price, add resellers,
                add agents, add free VIPs, set templates, list/remove admins).
- admin       : full reseller (CBE/Telebirr, price, user price, referral link,
                downline stats). Cannot add other admins or agents.
- agent       : restricted panel — can ONLY add VIP users (who pay via the
                super admin's CBE/Telebirr). Cannot set payment info or prices.
"""

from typing import Optional
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.state import AdminStates
from utils.texts import (
    ADMIN_WELCOME,
    AGENT_WELCOME,
    VIP_WELCOME,
    VIP_PAYING_WELCOME,
)

router = Router()


# ── Welcome DM helper ────────────────────────────────────────────────────────

async def _send_welcome(bot, user_id: int, text: str) -> bool:
    """Best-effort welcome DM to a freshly-added admin/agent/VIP.

    A bot cannot initiate a chat with a user who has never pressed Start, so
    send_message raises (TelegramForbiddenError / "chat not found") in that
    case. We swallow it and return False so the caller can tell the super admin
    the person must open the bot first. Returns True only on successful delivery.
    """
    try:
        await bot.send_message(
            user_id, text, parse_mode="Markdown", disable_web_page_preview=True
        )
        return True
    except Exception:
        return False


def _delivery_note(delivered: bool) -> str:
    """One-line status appended to the super admin's 'added!' confirmation."""
    if delivered:
        return "📨 A welcome message was sent to them."
    return (
        "⚠️ Couldn't message them yet — ask them to open the bot (press *Start* / "
        "send /start) first."
    )


# ── Keyboards ──────────────────────────────────────────────────────────────────

def kb_admin_menu(is_super_admin: bool = False, is_agent: bool = False) -> types.InlineKeyboardMarkup:
    """Build the admin panel keyboard based on role.

    super_admin : full menu — CBE/Telebirr, price, user price, referral link,
                  stats, add reseller, add agent, add free VIP, list/remove, templates.
    admin       : reseller menu — CBE/Telebirr, price, user price, referral link, stats.
    agent       : restricted menu — ONLY add VIP + referral link + stats.
    """
    if is_super_admin:
        buttons = [
            [
                types.InlineKeyboardButton(text="🏦 Set CBE Account", callback_data="adm_set_cbe"),
                types.InlineKeyboardButton(text="📱 Set Telebirr", callback_data="adm_set_telebirr"),
            ],
            [
                types.InlineKeyboardButton(text="💵 Set Price", callback_data="adm_set_price"),
                types.InlineKeyboardButton(text="👤 Set User Price", callback_data="adm_set_user_price"),
            ],
            [
                types.InlineKeyboardButton(text="🔗 Referral Link", callback_data="adm_link"),
                types.InlineKeyboardButton(text="📊 Downline Stats", callback_data="adm_stats"),
            ],
            [
                types.InlineKeyboardButton(text="➕ Add Reseller", callback_data="adm_add"),
                types.InlineKeyboardButton(text="🤝 Add Agent", callback_data="adm_add_agent"),
            ],
            [
                types.InlineKeyboardButton(text="⭐ Add Free (VIP)", callback_data="adm_add_vip"),
                types.InlineKeyboardButton(text="👤 My Users", callback_data="adm_my_users"),
            ],
            [
                types.InlineKeyboardButton(text="👥 List Admins & Agents", callback_data="adm_list"),
                types.InlineKeyboardButton(text="🎛 Set Templates", callback_data="adm_set_templates"),
            ],
            [types.InlineKeyboardButton(text="🔙 Close Menu", callback_data="adm_close")],
        ]
    elif is_agent:
        # Agents: can ONLY add VIPs + see referral link, stats, and their users
        buttons = [
            [
                types.InlineKeyboardButton(text="⭐ Add VIP", callback_data="adm_add_vip"),
                types.InlineKeyboardButton(text="👤 My Users", callback_data="adm_my_users"),
            ],
            [
                types.InlineKeyboardButton(text="🔗 Referral Link", callback_data="adm_link"),
                types.InlineKeyboardButton(text="📊 Downline Stats", callback_data="adm_stats"),
            ],
            [types.InlineKeyboardButton(text="🔙 Close Menu", callback_data="adm_close")],
        ]
    else:
        # Regular reseller admin: full payment controls but no management tools
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
                types.InlineKeyboardButton(text="👤 Set User Price", callback_data="adm_set_user_price"),
            ],
            [
                types.InlineKeyboardButton(text="👤 My Users", callback_data="adm_my_users"),
            ],
            [types.InlineKeyboardButton(text="🔙 Close Menu", callback_data="adm_close")],
        ]

    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


def _role_flags(role: str, actor: int):
    """Return (is_super, is_agent) booleans from role + actor id."""
    is_super = role == "super_admin" or actor in settings.super_admins
    is_agent = (not is_super) and role == "agent"
    return is_super, is_agent


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
    """Show admin panel for admins, agents, and super admins."""
    actor = message.from_user.id
    is_super, is_agent = _role_flags(role, actor)
    if not is_super and role not in ("admin", "agent"):
        return

    await state.clear()
    await message.answer(
        "🛠 *Admin Dashboard*\n\nSelect an option below:",
        reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
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
    is_super, is_agent = _role_flags(role, actor)
    await callback.message.edit_text(
        "🛠 *Admin Dashboard*\n\nAction canceled. Select an option below:",
        reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
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
    if role not in ("super_admin", "admin", "agent") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    admin_doc = await billing.get_admin(actor)
    ref_code = admin_doc.get("ref_code", str(actor)) if admin_doc else str(actor)
    bot_username = (await callback.bot.me()).username or ""
    link = f"https://t.me/{bot_username}?start={ref_code}"

    text = (
        "🔗 *Your Referral Link*\n\n"
        f"`{link}`\n\n"
        "Share this link with your customers. Any new user who starts the bot using this link will be bound to your reseller account!"
    )
    is_super, is_agent = _role_flags(role, actor)
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
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
    if role not in ("super_admin", "admin", "agent") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    # Fix #4: answer immediately — Telegram gives ~15 s before the query expires.
    # The DB calls below can take several seconds, so we must answer first.
    await callback.answer()

    stats = await billing.get_downline_stats(actor)
    admin_doc = await billing.get_admin(actor)
    price = admin_doc.get("price_per_download", settings.PRICE_PER_DOWNLOAD) if admin_doc else settings.PRICE_PER_DOWNLOAD

    text = (
        "📊 *Your Performance*\n\n"
        f"👥 *Total Bound Users:* {stats['total_users']}\n"
        f"⏳ *Pending Deposits:* {stats['pending_deposits']}\n"
        f"💰 *Total Deposited:* {stats['total_collected']} Birr\n"
        f"💵 *Price Per Download:* {price} Birr\n"
    )
    is_super, is_agent = _role_flags(role, actor)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
            parse_mode="Markdown",
        )
    except Exception as e:
        # Fix #5: ignore "message is not modified" — happens when the stats
        # haven't changed since the last time the button was pressed.
        if "message is not modified" not in str(e).lower():
            raise


# ── CBE Account Setup (admin + super admin only) ──────────────────────────────

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

    actor = message.from_user.id
    is_super, is_agent = _role_flags(role, actor)
    await message.answer(
        f"✅ *CBE Account Updated!*\n\n• Name: `{name}`\n• Number: `{number}`",
        reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
        parse_mode="Markdown",
    )


# ── Telebirr Setup (admin + super admin only) ─────────────────────────────────

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

    actor = message.from_user.id
    is_super, is_agent = _role_flags(role, actor)
    await message.answer(
        f"✅ *Telebirr Account Updated!*\n\n• Name: `{name}`\n• Phone: `{number}`",
        reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
        parse_mode="Markdown",
    )


# ── Price Setup (admin + super admin only) ────────────────────────────────────

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

    actor = message.from_user.id
    is_super, is_agent = _role_flags(role, actor)
    await message.answer(
        f"✅ *Price Updated!*\n\nYour users will now be charged `{price}` Birr per card.",
        reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
        parse_mode="Markdown",
    )


# ── Per-User Price Override (admin + super admin only) ────────────────────────

@router.callback_query(F.data == "adm_set_user_price")
async def cb_set_user_price_start(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    """Step 1 — ask which of the admin's users to set a custom price for."""
    actor = callback.from_user.id
    if role not in ("super_admin", "admin") and actor not in settings.super_admins:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    await state.set_state(AdminStates.waiting_set_user_price)
    await state.update_data(set_user_price_step="user_id")
    await callback.message.edit_text(
        "👤 *Set a User's Price*\n\n"
        "Give one specific user their own per-card price (overrides your standard "
        "price just for them).\n\n"
        "Step 1/2: Send the Telegram User ID of one of your users, "
        "or forward a message from them:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_set_user_price)
async def state_set_user_price(
    message: types.Message,
    state: FSMContext,
    billing,
    role: str = "user",
):
    """Two-step handler: collect the target user ID, then the custom price."""
    actor = message.from_user.id
    is_super, is_agent = _role_flags(role, actor)
    data = await state.get_data()
    step = data.get("set_user_price_step", "user_id")

    if step == "user_id":
        # ── Step 1: resolve target user ID ──
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
                "⚠️ Invalid ID. Please send a numeric Telegram ID or forward a message:",
                reply_markup=kb_admin_cancel(),
            )

        # Regular admins may only price users in their own downline.
        if not is_super:
            owner = await billing.get_owner(target_id)
            if owner != actor:
                return await message.answer(
                    f"⚠️ User `{target_id}` is not one of your users. "
                    f"They must start the bot via your referral link first.",
                    reply_markup=kb_admin_cancel(),
                    parse_mode="Markdown",
                )

        current = await billing.get_user_price_override(target_id)
        current_str = f"`{current}` Birr" if current is not None else "_default (your price)_"
        await state.update_data(set_user_price_step="price", target_user_id=target_id)
        await message.answer(
            f"👤 *Set a User's Price*\n\n"
            f"Step 2/2: User `{target_id}` current override: {current_str}\n\n"
            f"Send the custom price in Birr (e.g. `5`, or `0` to make it free for "
            f"them), or send `default` to remove the override so they pay your "
            f"standard price:",
            reply_markup=kb_admin_cancel(),
            parse_mode="Markdown",
        )

    elif step == "price":
        # ── Step 2: parse the price (or "default" to clear) and save ──
        target_id = data.get("target_user_id")
        raw = (message.text or "").strip().lower()

        if raw in ("default", "clear", "none", "reset"):
            price: Optional[int] = None
        else:
            try:
                price = int(raw)
                if price < 0:
                    raise ValueError
            except ValueError:
                return await message.answer(
                    "⚠️ Invalid price. Send a non-negative integer (e.g. `5` or `0`), "
                    "or `default` to remove the override:",
                    reply_markup=kb_admin_cancel(),
                    parse_mode="Markdown",
                )

        require_owner = None if is_super else actor
        ok = await billing.set_user_price(target_id, price, require_owner=require_owner)
        await state.clear()

        if not ok:
            await message.answer(
                f"⚠️ Could not update user `{target_id}` — they may not be one of "
                f"your users (ask them to start the bot via your link first).",
                reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
                parse_mode="Markdown",
            )
            return

        if price is None:
            detail = "override removed — they now pay your standard price."
        elif price == 0:
            detail = "they now generate cards *for free*."
        else:
            detail = f"they now pay `{price}` Birr per card."
        await message.answer(
            f"✅ *User Price Updated!*\n\nUser `{target_id}`: {detail}",
            reply_markup=kb_admin_menu(is_super_admin=is_super, is_agent=is_agent),
            parse_mode="Markdown",
        )


# ── Super Admin: Add Reseller Admin ──────────────────────────────────────────

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

    bot_username = (await message.bot.me()).username or ""
    link = f"https://t.me/{bot_username}?start={target_id}"
    delivered = await _send_welcome(message.bot, target_id, ADMIN_WELCOME.format(link=link))
    await message.answer(
        f"✅ *Reseller Added!*\n\n"
        f"• User ID: `{target_id}`\n"
        f"• Role: `admin`\n"
        f"• Referral link: `{link}`\n\n"
        f"{_delivery_note(delivered)}",
        reply_markup=kb_admin_menu(is_super_admin=True),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# ── Super Admin: Add Agent ────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_add_agent")
async def cb_super_add_agent(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    """Add an agent — can only add VIPs, earns nothing, payments go to super admin."""
    actor = callback.from_user.id
    if role != "super_admin" and actor not in settings.super_admins:
        return await callback.answer("⛔ Super admin only.", show_alert=True)

    await state.set_state(AdminStates.waiting_add_agent_id)
    await callback.message.edit_text(
        "🤝 *Add New Agent*\n\n"
        "An agent can *only* add VIP users to the system.\n"
        "They *cannot* set their own CBE/Telebirr or prices.\n"
        "All payments from their VIPs go through the *super admin's* accounts.\n\n"
        "Send the Telegram User ID (numeric) or forward a message from the user:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_add_agent_id)
async def state_add_agent(
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

    await billing.upsert_agent(target_id, active=True)
    await state.clear()

    delivered = await _send_welcome(message.bot, target_id, AGENT_WELCOME)
    await message.answer(
        f"✅ *Agent Added!*\n\n"
        f"• User ID: `{target_id}`\n"
        f"• Role: `agent`\n"
        f"• Permissions: Add VIPs only\n"
        f"• Earnings: None (all payments go to you)\n\n"
        f"{_delivery_note(delivered)}",
        reply_markup=kb_admin_menu(is_super_admin=True),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# ── Add VIP User ──────────────────────────────────────────────────────────────
# Available to: super_admin (free VIP), agent (paying VIP via super admin's accounts)

@router.callback_query(F.data == "adm_add_vip")
async def cb_add_vip(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    actor = callback.from_user.id
    is_super, is_agent = _role_flags(role, actor)

    if not is_super and not is_agent:
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    if is_super:
        prompt = (
            "⭐ *Add Free (VIP) User*\n\n"
            "This user will be able to generate ID cards **100% free** for themselves, "
            "without needing an invitation link.\n"
            "They *cannot* invite downline members or access the admin panel.\n\n"
            "Send the Telegram User ID (numeric) or forward a message from the user:"
        )
    else:
        prompt = (
            "⭐ *Add VIP User*\n\n"
            "This user will get *direct bot access* without needing your referral link.\n"
            "They must still **deposit via CBE or Telebirr** and pay per download.\n"
            "All payments go through the system account — not to you.\n\n"
            "Send the Telegram User ID (numeric) or forward a message from the user:"
        )

    await state.set_state(AdminStates.waiting_add_vip_id)
    await callback.message.edit_text(
        prompt,
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_add_vip_id)
async def state_add_vip(
    message: types.Message,
    state: FSMContext,
    billing,
    role: str = "user",
):
    actor = message.from_user.id
    is_super, is_agent = _role_flags(role, actor)

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

    if is_super:
        # Super admin: completely free VIP
        await billing.upsert_vip(target_id, active=True)
        await state.clear()
        delivered = await _send_welcome(message.bot, target_id, VIP_WELCOME)
        await message.answer(
            f"✅ *Free (VIP) User Added!*\n\n"
            f"• User ID: `{target_id}`\n"
            f"• Role: `vip`\n"
            f"• Price: `0 Birr (Free)`\n\n"
            f"{_delivery_note(delivered)}",
            reply_markup=kb_admin_menu(is_super_admin=True),
            parse_mode="Markdown",
        )
    else:
        # Agent: bind the VIP to this agent.
        # Downline stats will show the user under this agent, but all payment accounts
        # and deposits automatically route to the primary super admin.
        ok = await billing.add_paying_vip(admin_id=actor, user_id=target_id)
        await state.clear()

        if not ok:
            await message.answer(
                f"⚠️ Could not add `{target_id}` as a VIP.\n"
                f"They may already be a registered admin, agent, or VIP.",
                reply_markup=kb_admin_menu(is_super_admin=False, is_agent=True),
                parse_mode="Markdown",
            )
            return

        delivered = await _send_welcome(message.bot, target_id, VIP_PAYING_WELCOME)
        await message.answer(
            f"✅ *VIP User Added!*\n\n"
            f"• User ID: `{target_id}`\n"
            f"• Access: ✅ Direct (no invite link needed)\n"
            f"• Payment: 💳 CBE / Telebirr required\n"
            f"• Price: `{settings.PRICE_PER_DOWNLOAD}` Birr per card\n\n"
            f"{_delivery_note(delivered)}",
            reply_markup=kb_admin_menu(is_super_admin=False, is_agent=True),
            parse_mode="Markdown",
        )


# ── Super Admin: List Admins & Agents & Remove ───────────────────────────────

@router.callback_query(F.data == "adm_list")
async def cb_super_list_admins(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    import html as _html  # stdlib html.escape — safe for Telegram HTML mode

    actor = callback.from_user.id
    if role != "super_admin" and actor not in settings.super_admins:
        return await callback.answer("⛔ Super admin only.", show_alert=True)

    # Fix #4-style: answer immediately so the query never times out while
    # list_admins() fetches data from the DB.
    await callback.answer()

    admins = await billing.list_admins()
    if not admins:
        await callback.message.edit_text("ℹ️ No admins registered yet.")
        return

    # Fix #1: build text in HTML mode and escape every dynamic value so that
    # special characters in admin IDs, role names, prices, etc. cannot break
    # Telegram's entity parser (the previous Markdown version failed at byte 208
    # when a value contained an underscore or backtick).
    lines = ["<b>👥 Registered Admins &amp; Agents:</b>\n"]
    keyboard = []

    for a in admins:
        aid = a["_id"]
        arole = a.get("role", "admin")
        active = a.get("active", True)
        status_str = "🟢 Active" if active else "🔴 Inactive"
        price = a.get("price_per_download", settings.PRICE_PER_DOWNLOAD)

        safe_aid   = _html.escape(str(aid))
        safe_role  = _html.escape(arole.replace("_", " "))
        safe_price = _html.escape(str(price))

        lines.append(
            f"• <code>{safe_aid}</code> (<i>{safe_role}</i>) — {status_str} — {safe_price} Birr"
        )

        if arole != "super_admin" and active:
            keyboard.append([
                types.InlineKeyboardButton(
                    text=f"🗑 Remove {aid}",
                    callback_data=f"adm_remove:{aid}",
                )
            ])

    keyboard.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="adm_cancel_input")])

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode="HTML",
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


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
    await callback.answer(f"Admin/Agent {target_id} deactivated.", show_alert=True)

    # Refresh list
    await cb_super_list_admins(callback, billing, role=role)


# ── Super Admin: Set Allowed Templates for an Admin ──────────────────────────

@router.callback_query(F.data == "adm_set_templates")
async def cb_set_templates_start(
    callback: types.CallbackQuery,
    state: FSMContext,
    role: str = "user",
):
    """Step 1 — ask super-admin which admin ID to configure."""
    actor = callback.from_user.id
    if role != "super_admin" and actor not in settings.super_admins:
        return await callback.answer("⛔ Super admin only.", show_alert=True)

    await state.set_state(AdminStates.waiting_set_templates)
    await state.update_data(set_templates_step="admin_id")
    await callback.message.edit_text(
        "🎛 *Set Allowed Templates*\n\n"
        "Step 1/2: Send the Telegram User ID of the admin you want to configure, "
        "or forward a message from them:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(AdminStates.waiting_set_templates)
async def state_set_templates(
    message: types.Message,
    state: FSMContext,
    billing,
    role: str = "user",
):
    """Two-step handler: first collects the target admin ID, then the template list."""
    data = await state.get_data()
    step = data.get("set_templates_step", "admin_id")

    if step == "admin_id":
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
                "⚠️ Invalid ID. Please send a numeric Telegram ID or forward a message:",
                reply_markup=kb_admin_cancel(),
            )

        admin_doc = await billing.get_admin(target_id)
        if not admin_doc:
            return await message.answer(
                f"⚠️ User `{target_id}` is not a registered admin.",
                reply_markup=kb_admin_cancel(),
                parse_mode="Markdown",
            )
        if target_id in settings.super_admins:
            return await message.answer(
                "⚠️ Cannot restrict a super admin's templates.",
                reply_markup=kb_admin_cancel(),
            )

        current = admin_doc.get("allowed_templates", "A,B")
        await state.update_data(set_templates_step="templates", target_admin_id=target_id)
        await message.answer(
            f"🎛 *Set Allowed Templates*\n\n"
            f"Step 2/2: Admin `{target_id}` currently has: `{current}`\n\n"
            f"Send the templates to allow, separated by commas.\n"
            f"Examples: `A` (only Template A) | `A,B` (both templates)\n\n"
            f"Available letters: *A*, *B*",
            reply_markup=kb_admin_cancel(),
            parse_mode="Markdown",
        )

    elif step == "templates":
        target_id = data.get("target_admin_id")
        raw = (message.text or "").strip().upper()

        valid = {"A", "B"}
        requested = {t.strip() for t in raw.split(",") if t.strip()}
        invalid = requested - valid
        if not requested or invalid:
            return await message.answer(
                f"⚠️ Invalid input. Only `A` and `B` are valid template letters. "
                f"Send something like `A` or `A,B`:",
                reply_markup=kb_admin_cancel(),
                parse_mode="Markdown",
            )

        updated = await billing.set_allowed_templates(target_id, raw)
        await state.clear()

        cleaned = ",".join(sorted(requested))
        status = "✅" if updated else "⚠️ (admin row not found)"
        await message.answer(
            f"{status} *Templates updated!*\n\n"
            f"Admin `{target_id}` — allowed templates: `{cleaned}`\n\n"
            f"Their users will only see and be able to use the configured templates.",
            reply_markup=kb_admin_menu(is_super_admin=True),
            parse_mode="Markdown",
        )


# ── My Users: tappable, paginated downline list + per-user management ─────────
# A reseller (super_admin / admin / agent) lists the end-users bound to them,
# free/VIP first. Super admins and admins can open a user to set/clear a custom
# per-card price; agents get an info-only card (they can't control pricing).

_USERS_PAGE = 8     # tappable user buttons per list page
_USERS_CAP = 200    # max users fetched for the panel (matches billing default)


def _user_is_free(row: dict) -> bool:
    """Free/VIP bucket: an active system VIP, or an explicit 0 price override."""
    is_vip = row.get("vip_role") == "vip" and bool(row.get("vip_active"))
    return is_vip or row.get("price_override") == 0


def _authorized_reseller(role: str, actor: int) -> bool:
    return role in ("super_admin", "admin", "agent") or actor in settings.super_admins


@router.callback_query(F.data.startswith("adm_my_users"))
async def cb_my_users(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    """Paginated, tappable list of the users bound to this reseller.

    callback.data is either 'adm_my_users' (page 0) or 'adm_my_users:<page>'.
    """
    actor = callback.from_user.id
    if not _authorized_reseller(role, actor):
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    # Answer early — DB fetches below can take a few seconds (see cb_admin_stats).
    await callback.answer()

    page = 0
    if ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    is_super, is_agent = _role_flags(role, actor)
    users = await billing.list_downline_users(actor, limit=_USERS_CAP)
    total = (await billing.get_downline_stats(actor))["total_users"]

    if not users:
        hint = (
            "Use ⭐ Add VIP to add your first user."
            if is_agent
            else "Share your 🔗 Referral Link to onboard customers."
        )
        await callback.message.edit_text(
            f"👤 *My Users*\n\nYou don't have any users yet.\n{hint}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Back", callback_data="adm_cancel_input")],
            ]),
            parse_mode="Markdown",
        )
        return

    free_count = sum(1 for u in users if _user_is_free(u))
    paying_count = len(users) - free_count

    pages = (len(users) + _USERS_PAGE - 1) // _USERS_PAGE
    page = min(page, pages - 1)
    start = page * _USERS_PAGE
    chunk = users[start:start + _USERS_PAGE]

    rows = []
    for u in chunk:
        uid = u["telegram_id"]
        name = (u.get("name") or "").strip() or str(uid)
        icon = "⭐" if _user_is_free(u) else "👤"
        label = f"{icon} {name} — {int(u.get('balance') or 0)}B"
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([types.InlineKeyboardButton(text=label, callback_data=f"adm_user:{uid}")])

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton(text="◀️ Prev", callback_data=f"adm_my_users:{page - 1}"))
        nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="adm_noop"))
        if page < pages - 1:
            nav.append(types.InlineKeyboardButton(text="Next ▶️", callback_data=f"adm_my_users:{page + 1}"))
        rows.append(nav)

    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="adm_cancel_input")])

    cap_note = "" if len(users) >= total else f" _(showing {len(users)} of {total})_"
    header = (
        f"👤 *My Users* — {total} total{cap_note}\n"
        f"⭐ Free/VIP: {free_count}   💵 Paying: {paying_count}\n\n"
        f"Tap a user to view details"
        + ("." if is_agent else " or set their price.")
    )
    try:
        await callback.message.edit_text(
            header,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="Markdown",
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "adm_noop")
async def cb_noop(callback: types.CallbackQuery):
    """No-op for the non-interactive page-indicator button."""
    await callback.answer()


async def _render_user_detail(
    callback: types.CallbackQuery,
    billing,
    is_agent: bool,
    uid: int,
    notice: str = "",
):
    """Edit the message in place to show one user's card + management buttons.

    Agents get an info-only card; super_admin / admin get Set Price / Make Free /
    Standard. The caller is responsible for callback.answer() and for the
    ownership gate (super admins bypass it).
    """
    import html as _html  # stdlib html.escape — safe for Telegram HTML mode

    card = await billing.get_user_card(uid)
    if card is None:
        return await callback.message.edit_text(
            "⚠️ That user no longer exists.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 Back to list", callback_data="adm_my_users")],
            ]),
        )

    name = _html.escape((card.get("name") or "").strip() or str(uid))
    balance = int(card.get("balance") or 0)
    override = card.get("price_override")
    is_vip = bool(card.get("is_vip"))
    eff_price = await billing.get_price_for_user(uid)

    if is_vip:
        price_line = "⭐ VIP — free (0 Birr/card)"
    elif eff_price == 0:
        price_line = "🆓 Free (0 Birr/card)"
    else:
        price_line = f"💵 {eff_price} Birr/card"
    if override is not None:
        price_line += f"  <i>(custom override: {_html.escape(str(override))})</i>"

    lines = [
        f"👤 <b>User {_html.escape(str(uid))}</b>",
        f"• Name: {name}",
        f"• Balance: <b>{balance}</b> Birr",
        f"• Price: {price_line}",
    ]
    if notice:
        lines += ["", notice]

    kb = []
    if not is_agent:  # super_admin / admin can manage per-user pricing
        kb.append([types.InlineKeyboardButton(text="💵 Set Price", callback_data=f"adm_uprice:{uid}")])
        kb.append([
            types.InlineKeyboardButton(text="🆓 Make Free", callback_data=f"adm_ufree:{uid}"),
            types.InlineKeyboardButton(text="↩️ Standard", callback_data=f"adm_ustd:{uid}"),
        ])
    kb.append([types.InlineKeyboardButton(text="🔙 Back to list", callback_data="adm_my_users")])

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb),
            parse_mode="HTML",
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            raise


@router.callback_query(F.data.startswith("adm_user:"))
async def cb_user_detail(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    actor = callback.from_user.id
    if not _authorized_reseller(role, actor):
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    try:
        uid = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return await callback.answer("Invalid user.", show_alert=True)

    is_super, is_agent = _role_flags(role, actor)
    # Non-super resellers may only open their own users.
    if not is_super and await billing.get_owner(uid) != actor:
        return await callback.answer("⛔ Not one of your users.", show_alert=True)

    await callback.answer()
    await _render_user_detail(callback, billing, is_agent, uid)


def _can_price(role: str, actor: int) -> bool:
    """Only super admins and reseller admins may change a user's price."""
    is_super, is_agent = _role_flags(role, actor)
    return not is_agent and (role in ("super_admin", "admin") or actor in settings.super_admins)


@router.callback_query(F.data.startswith("adm_uprice:"))
async def cb_user_set_price_preset(
    callback: types.CallbackQuery,
    state: FSMContext,
    billing,
    role: str = "user",
):
    """Jump straight into the price step of the existing set-user-price FSM,
    pre-targeting this user (reuses state_set_user_price's save path)."""
    actor = callback.from_user.id
    if not _can_price(role, actor):
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    try:
        uid = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return await callback.answer("Invalid user.", show_alert=True)

    is_super, _ = _role_flags(role, actor)
    if not is_super and await billing.get_owner(uid) != actor:
        return await callback.answer("⛔ Not one of your users.", show_alert=True)

    current = await billing.get_user_price_override(uid)
    current_str = f"`{current}` Birr" if current is not None else "_default (your price)_"
    await state.set_state(AdminStates.waiting_set_user_price)
    await state.update_data(set_user_price_step="price", target_user_id=uid)
    await callback.message.edit_text(
        f"👤 *Set Price for User* `{uid}`\n\n"
        f"Current override: {current_str}\n\n"
        f"Send the custom price in Birr (e.g. `5`, or `0` to make it free), "
        f"or send `default` to remove the override:",
        reply_markup=kb_admin_cancel(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm_ufree:"))
async def cb_user_make_free(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    actor = callback.from_user.id
    if not _can_price(role, actor):
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    try:
        uid = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return await callback.answer("Invalid user.", show_alert=True)

    is_super, is_agent = _role_flags(role, actor)
    ok = await billing.set_user_price(uid, 0, require_owner=None if is_super else actor)
    await callback.answer("🆓 Set to free." if ok else "Not one of your users.", show_alert=not ok)
    if ok:
        await _render_user_detail(callback, billing, is_agent, uid, notice="🆓 Updated: now free.")


@router.callback_query(F.data.startswith("adm_ustd:"))
async def cb_user_reset_price(
    callback: types.CallbackQuery,
    billing,
    role: str = "user",
):
    actor = callback.from_user.id
    if not _can_price(role, actor):
        return await callback.answer("⛔ Unauthorized", show_alert=True)

    try:
        uid = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return await callback.answer("Invalid user.", show_alert=True)

    is_super, is_agent = _role_flags(role, actor)
    ok = await billing.set_user_price(uid, None, require_owner=None if is_super else actor)
    await callback.answer(
        "↩️ Reset to standard price." if ok else "Not one of your users.", show_alert=not ok
    )
    if ok:
        await _render_user_detail(
            callback, billing, is_agent, uid, notice="↩️ Updated: now pays your standard price."
        )
