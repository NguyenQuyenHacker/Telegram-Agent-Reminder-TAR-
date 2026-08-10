import logging

from aiogram.types import InlineKeyboardMarkup, Message
from tenacity import retry, stop_after_attempt, wait_exponential

from app.telegram.bot import bot

log = logging.getLogger(__name__)

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)


@_retry
async def send_message(
    chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> Message:
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)


@_retry
async def edit_message(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    await bot.edit_message_text(
        text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
    )
