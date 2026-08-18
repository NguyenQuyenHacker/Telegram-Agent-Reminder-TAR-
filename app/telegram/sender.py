import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message
from tenacity import retry, stop_after_attempt, wait_exponential

# Telegram tự tắt "đang gõ…" sau ~5 giây, nên phải bắn lại trước mốc đó.
_TYPING_PERIOD = 4.0


# Telegram chập chờn thì thử lại, nhưng không quá lâu: người dùng đang chờ tin.
# reraise=True để hết lượt thử là ném lỗi GỐC của Telegram, không phải RetryError.
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
async def send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> Message:
    """Gửi tin qua ĐÚNG con bot của vai đang xử lý.

    `bot` là tham số bắt buộc chứ không lấy từ module: hai bot dùng chung hàm
    này, lấy nhầm là câu trả lời của client đi ra từ bot admin.

    Mặc định gửi text thuần. Không bật parse_mode toàn cục ở Bot(): hàm này còn
    gửi câu trả lời của LLM và nguyên văn dữ liệu người dùng — chỉ cần một dấu
    '<' hay '&' lọt vào là Telegram trả 400 và tin nhắn mất luôn. Nơi nào tự
    dựng HTML thì tự khai parse_mode và tự escape phần nội dung động.
    """
    return await bot.send_message(
        chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode
    )


@asynccontextmanager
async def typing(bot: Bot, chat_id: int) -> AsyncIterator[None]:
    """Giữ "đang gõ…" suốt lúc xử lý một lượt.

    Một lượt hỏi mất hơn hai chục giây, và trong suốt hai chục giây đó màn hình
    người dùng không có gì cả — "bot chết" và "bot đang tra" trông y hệt nhau.
    Đây là thứ rẻ nhất phân biệt được hai chuyện đó.

    NUỐT mọi lỗi: mất hiệu ứng gõ là mất một hiệu ứng, để nó ném ra là mất câu
    trả lời. Không dùng `send_message` (có retry) — chậm một nhịp gõ thì bỏ qua
    nhịp đó, thử lại là vô nghĩa.
    """

    async def beat() -> None:
        while True:
            with suppress(Exception):
                await bot.send_chat_action(chat_id, "typing")
            await asyncio.sleep(_TYPING_PERIOD)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
