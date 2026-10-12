from typing import Any, Callable, Dict, Awaitable
from aiogram import BaseMiddleware, types
from app.config import settings

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
            else:
                owner_admin_id = await billing.get_owner(user.id)
                if owner_admin_id is not None:
                    admin_doc = await billing.get_admin(owner_admin_id)

        # Inject into data
        data["role"] = role
        data["owner_admin_id"] = owner_admin_id
        data["admin_doc"] = admin_doc

        # Admins and super admins bypass owner-gate and group-gate
        if role in ("super_admin", "admin") or user.id in settings.super_admins:
            return await handler(event, data)

        # Normal user: Owner Gate check
        # A normal user with no owner is blocked for everything except /start
        is_start = False
        if event.message and event.message.text:
            text = event.message.text.strip()
            if text.startswith("/start"):
                is_start = True

        if owner_admin_id is None:
            if not is_start:
                msg = (
                    "⚠️ You do not have access to this bot.\n\n"
                    "Please contact your reseller to get an access link."
                )
                if event.message:
                    await event.message.answer(msg)
                elif event.callback_query:
                    await event.callback_query.answer(msg, show_alert=True)
                return  # Block update
            # Exempt /start from group-gate so unbound users can bind
            return await handler(event, data)

        # Group Gate check (applied to bound normal users)
        if not settings.REQUIRED_GROUP_ID:
            return await handler(event, data)

        try:
            member = await event.bot.get_chat_member(
                chat_id=settings.REQUIRED_GROUP_ID,
                user_id=user.id
            )

            # Allowed statuses: owner, creator, administrator, member, restricted (still in group)
            if member.status in ["owner", "creator", "administrator", "member", "restricted"]:
                return await handler(event, data)

            restriction_msg = (
                "⚠️ This bot is only available for members of our official group.\n\n"
                "Please join the group first to use the service."
            )

            if event.message:
                await event.message.answer(restriction_msg)
            elif event.callback_query:
                await event.callback_query.answer(restriction_msg, show_alert=True)

            return  # Block update

        except Exception as e:
            print(f"❌ Membership Error for User {user.id} in Chat {settings.REQUIRED_GROUP_ID}: {e}")

            error_msg = (
                "⚠️ Bot configuration error.\n"
                "The membership check failed. Please contact the administrator."
            )

            if event.message:
                await event.message.answer(error_msg)
            elif event.callback_query:
                await event.callback_query.answer(error_msg, show_alert=True)

            return  # Block update
