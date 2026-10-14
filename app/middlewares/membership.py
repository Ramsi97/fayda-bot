from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware, types
from app.config import settings
from utils.texts import NO_ACCESS

class MembershipMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.Update,
        data: Dict[str, Any]
    ) -> Any:
        # Get user from event
        user = None
        if event.message:
            user = event.message.from_user
        elif event.callback_query:
            user = event.callback_query.from_user

        if not user:
            return await handler(event, data)

        billing = data.get("billing")
        role = "user"
        owner_admin_id = None
        admin_doc = None

        if billing is not None:
            role = await billing.get_role(user.id)
            if role in ("super_admin", "admin"):
                admin_doc = await billing.get_admin(user.id)
            elif role in ("vip", "agent"):
                # VIP/agent: free or restricted-panel users — no admin_doc needed
                pass
            else:
                owner_admin_id = await billing.get_owner(user.id)
                if owner_admin_id is not None:
                    admin_doc = await billing.get_admin(owner_admin_id)

        # Inject into data
        data["role"] = role
        data["owner_admin_id"] = owner_admin_id
        data["admin_doc"] = admin_doc

        # Super admins and admins bypass all gates
        if role in ("super_admin", "admin") or user.id in settings.super_admins:
            return await handler(event, data)

        # VIP users bypass the owner-gate — they use the bot for free but have no
        # reseller/admin panel access (handlers check role != 'vip').
        if role == "vip" or user.id in settings.vip_users:
            return await handler(event, data)

        # Agents bypass the owner-gate — they access the restricted agent panel.
        if role == "agent":
            return await handler(event, data)

        # Owner Gate — access is fully database-driven: a normal user must be
        # bound to a reseller admin (via their invite link). An unbound user is
        # blocked for everything except /start, which is exempt so they can bind.
        is_start = False
        if event.message and event.message.text:
            text = event.message.text.strip()
            if text.startswith("/start"):
                is_start = True

        if owner_admin_id is None:
            if not is_start:
                msg = NO_ACCESS.format(contact=settings.support_contact)
                if event.message:
                    await event.message.answer(msg)
                elif event.callback_query:
                    await event.callback_query.answer(msg, show_alert=True)
                return  # Block update
            return await handler(event, data)

        # Bound normal user — DB binding is the only access gate, so allow through.
        return await handler(event, data)

