"""Chế độ long-polling cho dev local.

Máy local không có URL public HTTPS nên Telegram không gọi được webhook. Khi
TELEGRAM_WEBHOOK_URL trống, app tự chuyển sang polling cho CẢ HAI bot: aiogram
chủ động gọi getUpdates rồi đẩy update vào ĐÚNG các handler mà webhook dùng,
nên luồng xử lý y hệt production, chỉ khác đường update đi vào.
"""

import asyncio
from contextlib import suppress

from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Message
from fastapi import FastAPI

from app.routers.webhooks import HANDLERS
from app.telegram.bots import ALL_ROLES, BotRole


def _build_dispatcher(app: FastAPI, role: BotRole) -> Dispatcher:
    # Cùng bảng handler mà endpoint webhook dùng: luồng xử lý y hệt production.
    handle = HANDLERS[role.name]["message"]
    handle_cb = HANDLERS[role.name]["callback"]
    dispatcher = Dispatcher()

    @dispatcher.message()
    async def _on_message(message: Message) -> None:
        await handle(app, message)

    @dispatcher.callback_query()
    async def _on_callback(callback: CallbackQuery) -> None:
        await handle_cb(app, callback)

    return dispatcher


async def start_dev_polling(app: FastAPI) -> list[tuple[Dispatcher, asyncio.Task[None]]]:
    running = []
    for role in ALL_ROLES:
        # Telegram không cho vừa webhook vừa getUpdates -> gỡ webhook cũ nếu còn
        await role.bot.delete_webhook(drop_pending_updates=True)
        dispatcher = _build_dispatcher(app, role)
        task = asyncio.create_task(
            dispatcher.start_polling(
                role.bot, handle_signals=False, close_bot_session=False
            )
        )
        running.append((dispatcher, task))
    return running


async def stop_dev_polling(
    running: list[tuple[Dispatcher, asyncio.Task[None]]],
) -> None:
    for dispatcher, task in running:
        # RuntimeError("Polling is not started"): --reload tắt app trong lúc
        # start_polling() còn chưa kịp chạy tới chỗ đánh dấu "đã bắt đầu".
        # Ném ra ở đây làm hỏng nốt phần dọn dẹp còn lại của lifespan (pool
        # chẳng hạn), mà bản thân nó không có gì để sửa — polling chưa chạy thì
        # cũng chẳng có gì phải dừng.
        with suppress(RuntimeError):
            await dispatcher.stop_polling()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
