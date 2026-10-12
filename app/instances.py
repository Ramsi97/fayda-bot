# app/instances.py
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import settings
from aiogram.client.session.aiohttp import AiohttpSession

from services.processing_service import ProcessingService
from services.billing_service import BillingService

# Set timeout for Telegram API requests (seconds)
session = AiohttpSession(timeout=300.0)

bot = Bot(token=settings.TELEGRAM_TOKEN, session=session)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Billing (MongoDB-backed balances). init_db() is awaited at startup
# (app/main.py lifespan and run_local.py) before any update is handled.
billing = BillingService(settings.MONGODB_URI, settings.MONGODB_DB)

# Singleton processor to ensure the lock is shared globally. It charges the
# user's balance (via billing) only after a card is successfully delivered.
processor = ProcessingService(bot=bot, billing=billing)