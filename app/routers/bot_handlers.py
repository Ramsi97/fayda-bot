import asyncio
from datetime import datetime, timedelta
from typing import Dict

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext

from app.state import FaydaBotStates
from app.config import settings
from core.api.fayda_client import FaydaSession
from utils.texts import (
    BATCH_NEXT_PROMPT,
    BATCH_START_PROMPT,
    FAN_PROMPT,
    FIN_PROMPT,
    INSUFFICIENT_BALANCE,
    NO_ACCESS,
    OTP_INVALID,
    OTP_PROMPT,
    WELCOME_TEXT,
)

router = Router()

# In-memory storage for active sessions (holding active httpx client connections)
_session_cache: Dict[int, FaydaSession] = {}
_batch_session_cache: Dict[int, Dict[int, FaydaSession]] = {}


# ── Keyboards ──────────────────────────────────────────────────────────────────

def kb_main():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📄 One ID"), types.KeyboardButton(text="📚 Multiple IDs")],
            [types.KeyboardButton(text="💰 ገንዘብ ማስገባት"), types.KeyboardButton(text="💰 ቀሪ ሒሳብ")],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def kb_color():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🎨 Color"), types.KeyboardButton(text="⚫ Black & White")],
            [types.KeyboardButton(text="🔙 Back to Menu")],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def kb_template(allowed: list[str] | None = None):
    """Template selection keyboard. `allowed` limits which buttons appear."""
    all_templates = [("🆔 Template A", "A"), ("🆔 Template B", "B")]
    if allowed is None:
        allowed = ["A", "B"]
    buttons = [
        types.KeyboardButton(text=label)
        for label, key in all_templates
        if key in allowed
    ]
    return types.ReplyKeyboardMarkup(
        keyboard=[buttons, [types.KeyboardButton(text="🔙 Back to Menu")]],
        resize_keyboard=True,
        persistent=True,
    )


def kb_collecting(count: int):
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text=f"✅ Done ({count} IDs collected)")],
            [types.KeyboardButton(text="🔙 Back to Menu")],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def kb_cancel():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
        resize_keyboard=True,
        persistent=True,
    )


# ── Command & Menu Navigation ──────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(
    message: types.Message,
    state: FSMContext,
    billing,
    command: CommandObject = None,
    role: str = "user",
    owner_admin_id: int = None,
):
    await _cleanup_user_sessions(message.from_user.id)
    await state.clear()

    # 1. Already admin, agent, or VIP: normal menu, no binding
    if role in ("super_admin", "admin", "agent", "vip"):
        await billing.ensure_user(message.from_user.id, message.from_user.full_name)
        await message.answer(
            WELCOME_TEXT,
            reply_markup=kb_main(),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    # 2. Already has owner: normal menu
    if owner_admin_id is not None:
        await billing.ensure_user(message.from_user.id, message.from_user.full_name)
        await message.answer(
            WELCOME_TEXT,
            reply_markup=kb_main(),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    # 3. No owner yet: check ref payload
    ref_code = command.args.strip() if (command and command.args) else None
    admin_doc = await billing.get_admin_by_ref(ref_code) if ref_code else None

    if admin_doc and admin_doc.get("active", True):
        # Prevent self-binding or invalid binding
        target_admin_id = admin_doc["_id"]
        if target_admin_id != message.from_user.id:
            await billing.bind_user_owner(message.from_user.id, target_admin_id)
            await billing.ensure_user(message.from_user.id, message.from_user.full_name)
            await message.answer(
                WELCOME_TEXT,
                reply_markup=kb_main(),
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            return

    # 4. No owner + missing/invalid/inactive ref: blocked message, return before ensure_user
    await message.answer(NO_ACCESS.format(contact=settings.support_contact))
    return


@router.message(Command("cancel"))
@router.message(F.text == "❌ Cancel")
@router.message(F.text == "🔙 Back to Menu")
async def cmd_cancel(message: types.Message, state: FSMContext):
    await _cleanup_user_sessions(message.from_user.id)
    await state.clear()
    await message.answer("🔙 Returned to main menu.", reply_markup=kb_main())


# ── Mode Selection ─────────────────────────────────────────────────────────────

@router.message(F.text == "📄 One ID")
async def mode_single(message: types.Message, state: FSMContext, billing):
    if not await _ensure_affordable(message, billing):
        return
    await state.update_data(mode="single")
    await state.set_state(FaydaBotStates.choosing_color)
    await message.answer("🎨 Select output color type:", reply_markup=kb_color())


@router.message(F.text == "📚 Multiple IDs")
async def mode_multiple(message: types.Message, state: FSMContext, billing):
    if not await _ensure_affordable(message, billing):
        return
    await state.update_data(mode="multiple", batch_items=[])
    await state.set_state(FaydaBotStates.choosing_color)
    await message.answer("🎨 Select output color type:", reply_markup=kb_color())


# ── Color Selection ────────────────────────────────────────────────────────────

@router.message(FaydaBotStates.choosing_color, F.text.in_(["🎨 Color", "⚫ Black & White"]))
async def choose_color(message: types.Message, state: FSMContext, billing):
    is_color = message.text == "🎨 Color"
    await state.update_data(is_color=is_color)
    await state.set_state(FaydaBotStates.choosing_template)
    allowed = await billing.get_allowed_templates(message.from_user.id)
    await message.answer("🆔 Select ID card template:", reply_markup=kb_template(allowed))


# ── Template Selection ─────────────────────────────────────────────────────────

@router.message(FaydaBotStates.choosing_template, F.text.in_(["🆔 Template A", "🆔 Template B"]))
async def choose_template(message: types.Message, state: FSMContext, billing):
    requested = "B" if "Template B" in message.text else "A"

    # Check what templates this user is allowed to use.
    allowed = await billing.get_allowed_templates(message.from_user.id)

    if requested not in allowed:
        allowed_str = " and ".join(f"Template {t}" for t in allowed)
        await message.answer(
            f"⛔ Template {requested} is not available for your account.\n"
            f"You can use: {allowed_str}.",
            reply_markup=kb_template(allowed),
        )
        return

    await state.update_data(template=requested)

    data = await state.get_data()
    mode = data.get("mode", "single")

    if mode == "single":
        await state.set_state(FaydaBotStates.waiting_fan)
        await message.answer(FAN_PROMPT, reply_markup=kb_cancel(), parse_mode="Markdown")
    else:
        await state.set_state(FaydaBotStates.batch_collecting)
        await message.answer(BATCH_START_PROMPT, reply_markup=kb_collecting(0), parse_mode="Markdown")


# ── SINGLE FLOW: FAN -> FIN -> OTP -> ID ──────────────────────────────────────

@router.message(FaydaBotStates.waiting_fan, F.text)
async def single_receive_fan(message: types.Message, state: FSMContext):
    fan = message.text.strip().replace(" ", "")
    if not fan or len(fan) < 6:
        return await message.answer("⚠️ Please enter a valid FAN number.")

    await state.update_data(fan=fan)
    await state.set_state(FaydaBotStates.waiting_fin)
    await message.answer(FIN_PROMPT, reply_markup=kb_cancel(), parse_mode="Markdown")


@router.message(FaydaBotStates.waiting_fin, F.text)
async def single_receive_fin(message: types.Message, state: FSMContext, processor):
    fin = message.text.strip().replace(" ", "")
    if not fin:
        return await message.answer("⚠️ Please enter a valid FIN number.")

    data = await state.get_data()
    fan = data.get("fan", "")

    status = await message.answer("⏳ Sending OTP to your registered phone...")

    try:
        session = await processor.initiate_otp(fan)
    except Exception as e:
        # Fix #3: edit_text only accepts InlineKeyboardMarkup — passing
        # kb_cancel() (a ReplyKeyboardMarkup) raises a pydantic ValidationError.
        # Drop reply_markup here; the user's existing reply keyboard stays intact.
        await status.edit_text(
            f"❌ Failed to request OTP: {e}\n\nPlease check your FAN number and try again.",
        )
        return

    _session_cache[message.from_user.id] = session
    await state.update_data(fin=fin, status_msg_id=status.message_id)
    await state.set_state(FaydaBotStates.waiting_otp)
    await status.edit_text(OTP_PROMPT, parse_mode="Markdown")


@router.message(FaydaBotStates.waiting_otp, F.text)
async def single_receive_otp(message: types.Message, state: FSMContext, processor):
    otp = message.text.strip()
    data = await state.get_data()
    fan = data.get("fan", "")
    fin = data.get("fin", "")
    is_color = data.get("is_color", True)
    template = data.get("template", "A")
    status_msg_id = data.get("status_msg_id")

    session = _session_cache.pop(message.from_user.id, None)
    if session is None:
        await message.answer("⚠️ Session expired. Please start again from the menu.", reply_markup=kb_main())
        await state.clear()
        return

    await state.clear()

    success = await processor.verify_and_generate(
        session=session,
        otp=otp,
        fin=fin,
        fan=fan,
        chat_id=message.chat.id,
        color=is_color,
        template=template,
        status_message_id=status_msg_id,
        user_id=message.from_user.id,
    )

    if success:
        await message.answer("📋 ID Card generated! What would you like to do next?", reply_markup=kb_main())
    else:
        await message.answer("❌ Could not generate your ID card. Please check the details and try again.", reply_markup=kb_main())


# ── BATCH FLOW: Multi (FAN -> FIN -> OTP) -> A4 Canvas ────────────────────────

@router.message(FaydaBotStates.batch_collecting, F.text)
async def batch_collect_fan(message: types.Message, state: FSMContext, processor):
    text = message.text.strip()

    if text.startswith("✅ Done"):
        return await batch_process_all(message, state, processor)

    fan = text.replace(" ", "")
    if not fan or len(fan) < 6:
        return await message.answer("⚠️ Please enter a valid FAN number or press ✅ Done.")

    data = await state.get_data()
    batch_items = data.get("batch_items", [])
    item_num = len(batch_items) + 1

    await state.update_data(current_batch_fan=fan)
    await state.set_state(FaydaBotStates.batch_waiting_fin)
    await message.answer(
        f"🔢 Enter the *12-digit FIN number* for ID #{item_num} (FAN: `{fan}`):",
        parse_mode="Markdown",
        reply_markup=kb_cancel(),
    )


@router.message(FaydaBotStates.batch_waiting_fin, F.text)
async def batch_receive_fin(message: types.Message, state: FSMContext, processor):
    fin = message.text.strip().replace(" ", "")
    if not fin:
        return await message.answer("⚠️ Please enter a valid FIN number.")

    data = await state.get_data()
    fan = data.get("current_batch_fan", "")
    batch_items = data.get("batch_items", [])
    idx = len(batch_items)

    status = await message.answer(f"⏳ Sending OTP for ID #{idx+1} (`{fan}`)...")

    try:
        session = await processor.initiate_otp(fan)
    except Exception as e:
        # Fix #3 (batch): edit_text cannot take a ReplyKeyboardMarkup.
        await status.edit_text(
            f"❌ Failed to request OTP for `{fan}`: {e}\n\nPlease try this ID again.",
            parse_mode="Markdown",
        )
        await state.set_state(FaydaBotStates.batch_collecting)
        return

    _batch_session_cache.setdefault(message.from_user.id, {})[idx] = session
    await state.update_data(
        current_batch_fin=fin,
        current_batch_idx=idx,
        batch_status_msg_id=status.message_id,
    )
    await state.set_state(FaydaBotStates.batch_waiting_otp)
    await status.edit_text(
        f"📱 OTP sent for ID #{idx+1} (`{fan}`).\nPlease enter the *OTP code*:",
        parse_mode="Markdown",
    )


@router.message(FaydaBotStates.batch_waiting_otp, F.text)
async def batch_receive_otp(message: types.Message, state: FSMContext, processor):
    otp = message.text.strip()
    data = await state.get_data()
    idx = data.get("current_batch_idx", 0)
    fan = data.get("current_batch_fan", "")
    fin = data.get("current_batch_fin", "")
    batch_items = data.get("batch_items", [])
    status_msg_id = data.get("batch_status_msg_id")

    session = _batch_session_cache.get(message.from_user.id, {}).get(idx)
    if session is None:
        await message.answer("⚠️ Session expired. Please send the FAN again.")
        await state.set_state(FaydaBotStates.batch_collecting)
        return

    # Verify OTP immediately
    try:
        await processor.verify_otp_only(session, otp)
    except ValueError as e:
        await message.answer(f"❌ {e}\nPlease enter the correct OTP or /cancel:", parse_mode="Markdown")
        return

    # OTP verified! Add to verified batch items
    batch_items.append({
        "idx": idx,
        "fan": fan,
        "fin": fin,
        "verified": True,
    })
    await state.update_data(batch_items=batch_items)

    if status_msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_msg_id,
                text=f"✅ ID #{len(batch_items)} (`{fan}`) verified!",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    await state.set_state(FaydaBotStates.batch_collecting)
    await message.answer(
        f"✅ ID #{len(batch_items)} collected!\n\n{BATCH_NEXT_PROMPT}",
        parse_mode="Markdown",
        reply_markup=kb_collecting(len(batch_items)),
    )


async def batch_process_all(message: types.Message, state: FSMContext, processor):
    """Process all verified sessions and tile onto A4 pages."""
    data = await state.get_data()
    batch_items = data.get("batch_items", [])
    is_color = data.get("is_color", True)
    template = data.get("template", "A")

    sessions_map = _batch_session_cache.pop(message.from_user.id, {})

    if not batch_items:
        await message.answer("⚠️ No IDs have been collected yet.", reply_markup=kb_main())
        await state.clear()
        return

    await state.clear()

    entries = []
    for item in batch_items:
        sess = sessions_map.get(item["idx"])
        if sess:
            entries.append({
                "session": sess,
                "fan": item["fan"],
                "fin": item["fin"],
                "verified": True,
                "otp": None,
            })

    if not entries:
        await message.answer("❌ No active sessions found. Please try again.", reply_markup=kb_main())
        return

    status = await message.answer(
        f"🚀 Generating {len(entries)} ID cards and formatting onto A4... Please wait.",
        reply_markup=kb_main(),
    )

    await processor.verify_and_generate_batch(
        entries=entries,
        chat_id=message.chat.id,
        color=is_color,
        template=template,
        status_message_id=status.message_id,
        user_id=message.from_user.id,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _ensure_affordable(message: types.Message, billing) -> bool:
    """Ensure the user exists and can afford at least one card.

    Returns True if the flow may proceed. Otherwise shows the insufficient-balance
    prompt (pointing at 💰 ገንዘብ ማስገባት) and returns False.
    """
    user = message.from_user
    await billing.ensure_user(user.id, user.full_name)
    price = await billing.get_price_for_user(user.id)
    if price <= 0:
        return True
    bal = await billing.get_balance(user.id)
    if bal < price:
        await message.answer(
            INSUFFICIENT_BALANCE.format(balance=bal, price=price),
            parse_mode="Markdown",
            reply_markup=kb_main(),
        )
        return False
    return True


async def _cleanup_user_sessions(user_id: int):
    """Safely close any active sessions for a user."""
    sess = _session_cache.pop(user_id, None)
    if sess:
        try:
            await sess.close()
        except Exception:
            pass

    batch_map = _batch_session_cache.pop(user_id, {})
    for s in batch_map.values():
        try:
            await s.close()
        except Exception:
            pass


@router.message()
async def catch_all(message: types.Message):
    await message.answer("Please select an option or use /start to begin.", reply_markup=kb_main())