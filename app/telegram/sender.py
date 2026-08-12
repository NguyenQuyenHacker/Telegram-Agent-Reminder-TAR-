import logging

from aiogram.types import InlineKeyboardMarkup, Message
from tenacity import retry, stop_after_attempt, wait_exponential

from app.telegram.bot import bot

log = logging.getLogger(__name__)

# Telegram chập chờn thì thử lại, nhưng không quá lâu: người dùng đang chờ tin.
_MAX_SEND_ATTEMPTS = 3
_RETRY_BACKOFF_MULTIPLIER = 1
_RETRY_MIN_WAIT_SECONDS = 1
_RETRY_MAX_WAIT_SECONDS = 8

_retry = retry(
    stop=stop_after_attempt(_MAX_SEND_ATTEMPTS),
    wait=wait_exponential(
        multiplier=_RETRY_BACKOFF_MULTIPLIER,
        min=_RETRY_MIN_WAIT_SECONDS,
        max=_RETRY_MAX_WAIT_SECONDS,
    ),
    reraise=True,
)


@_retry
async def send_message(
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> Message:
    """Mặc định gửi text thuần.

    Không bật parse_mode toàn cục ở Bot(): hàm này còn gửi câu trả lời của LLM
    và nguyên văn báo cáo người dùng: chỉ cần một dấu '<' hay '&' lọt vào là
    Telegram trả 400 và tin nhắn mất luôn. Nơi nào tự dựng HTML thì tự khai
    parse_mode="HTML", và tự escape phần nội dung động.
    """
    return await bot.send_message(
        chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode
    )


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
