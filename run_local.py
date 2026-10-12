# run_local.py
"""
Local development runner using Telegram Long-Polling.
No webhook URL or ngrok needed!

Usage:
    /home/ramsi/telegram_bot/myenv/bin/python3 run_local.py
"""

import asyncio
import logging
import sys

from app.config import settings
from app.instances import bot, dp, processor, scheduler, billing
from app.middlewares.membership import MembershipMiddleware
from app.routers.bot_handlers import router as bot_router
from app.routers.payment_handlers import router as payment_router
from app.routers.admin_handlers import router as admin_router


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger = logging.getLogger("LocalBot")

    logger.info("🔧 Starting bot in Long-Polling mode for local testing...")

    # Drop any existing webhook so polling can receive updates
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🗑️ Deleted old webhook (if any) and dropped pending updates.")

    # Register Middlewares
    dp.update.outer_middleware(MembershipMiddleware())

    # Include routers (payment & admin first so their handlers take priority over catch-all)
    dp.include_router(payment_router)
    dp.include_router(admin_router)
    dp.include_router(bot_router)

    # Start APScheduler for batch timeouts
    scheduler.start()
    logger.info("⏰ Scheduler started.")

    # Open the billing DB (balances / deposits) before polling.
    await billing.init_db()
    logger.info("💰 Billing DB ready.")

    bot_info = await bot.get_me()
    dp["bot_username"] = bot_info.username
    logger.info(f"🤖 Connected to Telegram bot: @{bot_info.username} ({bot_info.first_name})")
    print(f"\n=======================================================")
    print(f"🚀 Bot is RUNNING! Chat with @{bot_info.username} on Telegram.")
    print(f"Press Ctrl+C to stop.")
    print(f"=======================================================\n")

    try:
        # Start polling loop
        await dp.start_polling(bot, processor=processor, billing=billing, scheduler=scheduler)
    finally:
        scheduler.shutdown()
        await billing.close()
        await bot.session.close()
        logger.info("👋 Bot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot stopped.")
