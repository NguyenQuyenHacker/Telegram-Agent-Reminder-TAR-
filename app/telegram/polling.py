"""Chế độ long-polling cho dev local.

Máy local không có URL public HTTPS nên Telegram không gọi được webhook. Khi
TELEGRAM_WEBHOOK_URL trống, app tự chuyển sang polling: aiogram chủ động gọi
getUpdates rồi đẩy update vào ĐÚNG các handler mà webhook dùng, nên luồng xử lý
y hệt production, chỉ khác đường update đi vào.
"""

import asyncio
import logging
from contextlib import suppress

from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Message
from fastapi import FastAPI

from app.routers.webhooks import handle_task_callback, handle_text_message
from app.telegram.bot import bot

log = logging.getLogger(__name__)


def _build_dispatcher(app: FastAPI) -> Dispatcher:
    dp = Dispatcher()

    @dp.message()
    async def _on_message(message: Message) -> None:
        if message.text:
            await handle_text_message(app.state.graph, message)

    @dp.callback_query()
    async def _on_callback(cb: CallbackQuery) -> None:
        if (cb.data or "").startswith("task:"):
            await handle_task_callback(cb)

    return dp


async def start_dev_polling(app: FastAPI) -> tuple[Dispatcher, asyncio.Task]:
    # Telegram không cho vừa webhook vừa getUpdates -> gỡ webhook cũ nếu còn
    await bot.delete_webhook(drop_pending_updates=True)
    dp = _build_dispatcher(app)
    task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False, close_bot_session=False)
    )
    log.info("Dev polling: đang lắng nghe tin nhắn qua getUpdates")
    return dp, task


async def stop_dev_polling(dp: Dispatcher, task: asyncio.Task) -> None:
    await dp.stop_polling()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
