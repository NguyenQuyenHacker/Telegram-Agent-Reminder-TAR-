import logging
from typing import Any

from aiogram.types import Message, Update
from fastapi import APIRouter, Header, HTTPException, Request
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.core.security import verify_webhook_secret
from app.telegram.bot import bot
from app.telegram.media import IncomingMedia, download_media, extract_media
from app.telegram.messages import media_failed_text, media_understood_text
from app.telegram.sender import send_message
from reminder_agent.utils.intent import classify_message
from reminder_agent.utils.media_text import understand_media

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks")


def _thread_config(chat_id: int) -> dict[str, dict[str, str]]:
    """Mỗi chat là một thread riêng của LangGraph, state lưu theo thread_id này."""
    return {"configurable": {"thread_id": str(chat_id)}}


# Mọi node có interrupt() phải nằm trong tập này. Sót một cái là tin nhắn trả
# lời của người dùng bị classify_message coi như câu hỏi mới, còn graph thì treo
# ở interrupt mãi mãi.
_INTERRUPT_NODES = {"ask_confirm", "ask_update_confirm"}


async def _is_awaiting_confirm(graph: Any, chat_id: int) -> bool:
    snapshot = await graph.aget_state(_thread_config(chat_id))
    return bool(_INTERRUPT_NODES.intersection(snapshot.next or ()))


async def _start_report_turn(graph: Any, chat_id: int, text: str) -> None:
    await graph.ainvoke(
        {
            "chat_id": chat_id,
            "raw_report_text": text,
            "extracted_tasks": [],
            "tool_call_rounds": 0,
            "pending_updates": [],
            "pending_views": [],
        },
        config=_thread_config(chat_id),
    )


async def _start_question_turn(graph: Any, chat_id: int, text: str) -> None:
    # raw_report_text phải là None tường minh: giá trị cũ còn nằm trong
    # checkpoint sẽ khiến route_entry lôi câu hỏi sang nhánh trích xuất.
    # pending_updates và pending_views cũng vậy: sót lại từ lượt trước là lượt
    # này vừa trả lời xong đã hỏi duyệt một đề xuất, hoặc kèm bảng chi tiết của
    # một đầu việc người dùng không hề nhắc tới.
    await graph.ainvoke(
        {
            "chat_id": chat_id,
            "raw_report_text": None,
            "messages": [HumanMessage(content=text)],
            "tool_call_rounds": 0,
            "pending_updates": [],
            "pending_views": [],
        },
        config=_thread_config(chat_id),
    )


async def _dispatch(graph: Any, chat_id: int, text: str, kind: str) -> None:
    """Mở một lượt mới cho graph, theo nhánh báo cáo hay nhánh hỏi đáp.

    Tách khỏi handle_text_message để đầu vào ảnh/thoại đi đúng đường mà tin nhắn
    gõ tay đi: sau khi đọc ra text thì hai loại đầu vào không còn khác gì nhau.
    """
    log.info("MSG kind=%s chat_id=%s", kind, chat_id)
    if kind == "report":
        await _start_report_turn(graph, chat_id, text)
    else:
        await _start_question_turn(graph, chat_id, text)


async def handle_text_message(graph: Any, message: Message) -> None:
    """Job C: tin nhắn text. Ưu tiên luồng xác nhận đang treo, rồi mới phân loại."""
    chat_id = message.chat.id
    text = message.text

    # Đang chờ duyệt thì tin nhắn này là câu trả lời cho bảng đầu việc, không
    # phải yêu cầu mới. Đẩy nguyên văn vào graph; đọc ý là việc của read_decision
    # (ở trong graph nên được Langfuse trace, khác với hồi còn xử lý tại đây).
    if await _is_awaiting_confirm(graph, chat_id):
        await graph.ainvoke(Command(resume=text), config=_thread_config(chat_id))
        return

    await _dispatch(graph, chat_id, text, classify_message(text))


# Ý định do model đọc ảnh/thoại trả về -> nhánh của graph. Cùng tên với giá trị
# classify_message trả ra, để _dispatch chỉ phải biết một bộ từ.
_MEDIA_INTENT_KINDS = {"tasks": "report", "question": "question"}


async def _read_media(chat_id: int, media: IncomingMedia) -> dict | None:
    """Tải file rồi đọc ra {"intent", "text"}. None nghĩa là đã nói lời từ chối.

    Mọi lỗi đều rơi về cùng một câu trả lời: người dùng chỉ cần biết bot không
    đọc được và gõ lại giúp, còn nguyên nhân (file to, Telegram trả rỗng, model
    hỏng) thì nằm ở log.
    """
    try:
        data = await download_media(media)
        understanding = (
            await understand_media(media.kind, data, media.mime_type, media.caption)
            if data
            else None
        )
    except Exception:
        log.exception("MEDIA: không xử lý được %s", media.kind)
        understanding = None

    if not understanding or not understanding["text"]:
        await send_message(chat_id, media_failed_text(media.kind))
        return None

    # Cho người dùng đọc lại thứ bot nghe/đọc được TRƯỚC khi nó hành động: nghe
    # nhầm một cái tên hay một con số thì họ thấy ngay ở đây, chứ không phải đợi
    # tới lúc bảng đầu việc hiện ra với nội dung lạ.
    await send_message(chat_id, media_understood_text(media.kind, understanding["text"]))
    return understanding


async def handle_media_message(graph: Any, message: Message) -> None:
    """Job C: ảnh và tin nhắn thoại. Đọc ra text rồi đi tiếp đúng đường của text."""
    media = extract_media(message)
    if media is None:
        return

    chat_id = message.chat.id
    # Hỏi state TRƯỚC khi gọi model: đọc một file thoại mất vài giây, mà
    # _read_media còn gửi tin "tôi nghe được ..." vào giữa — nếu hỏi sau thì
    # khoảng chờ đó là khoảng state có thể đã đổi dưới chân mình.
    awaiting_confirm = await _is_awaiting_confirm(graph, chat_id)

    understanding = await _read_media(chat_id, media)
    if understanding is None:
        return

    # Đang chờ duyệt thì lời thoại này là câu trả lời — người dùng nói "ok" bằng
    # miệng cũng phải chốt được bảng đầu việc như gõ "ok".
    if awaiting_confirm:
        await graph.ainvoke(
            Command(resume=understanding["text"]), config=_thread_config(chat_id)
        )
        return

    # Ý định do model đọc nội dung quyết định, không phải classify_message: xem
    # reminder_agent/utils/media_text.py. Giá trị lạ thì coi như câu hỏi — nhánh
    # hỏi đáp không ghi gì vào DB nếu người dùng không gật.
    kind = _MEDIA_INTENT_KINDS.get(understanding["intent"], "question")
    await _dispatch(graph, chat_id, understanding["text"], kind)


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if not verify_webhook_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # gắn bot vào context để message.answer() dùng được
    update = Update.model_validate(await request.json(), context={"bot": bot})
    graph = request.app.state.graph

    # Không còn nút inline nào trong hệ thống: báo xong / hủy / đổi hạn đều là
    # tin nhắn thường, agent đọc ý rồi đề xuất. Update kiểu callback bị bỏ qua.
    if update.message:
        await route_message(graph, update.message)

    return {"ok": True}


async def route_message(graph: Any, message: Message) -> None:
    """Một tin nhắn Telegram -> đúng handler. Webhook và polling dùng chung."""
    if message.text:
        await handle_text_message(graph, message)
    else:
        await handle_media_message(graph, message)
