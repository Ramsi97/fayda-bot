# app/routers/webhook.py
import asyncio
from fastapi import APIRouter, Request, Depends
from aiogram import types
from app.instances import bot, dp, scheduler, billing
from app.dependencies import get_processing_service

router = APIRouter()
@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    processor=Depends(get_processing_service)
):
    try:
        update_data = await request.json()

        # Use model_validate instead of Update(**update_data)
        update = types.Update.model_validate(update_data, context={"bot": bot})

        # --- THE FIX: Non-blocking background task ---
        # We start the processing but return 200 OK to Telegram immediately.
        # This prevents Telegram from retrying if processing is slow.
        asyncio.create_task(
            dp.feed_update(
                bot,
                update,
                processor=processor,
                billing=billing,
                scheduler=scheduler,
                dp=dp
            )
        )
    except Exception as e:
        print(f"⚠️ Webhook Error: {e}")
        # We still return OK to Telegram so it doesn't retry infinitely on a bad update
    
    return {"ok": True}