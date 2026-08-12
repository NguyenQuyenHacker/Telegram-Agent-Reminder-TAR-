import logging
from typing import Any

from aiogram.types import Message, Update
from fastapi import APIRouter, Header, HTTPException, Request
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.core.security import verify_webhook_secret
from app.telegram.bot import bot
from reminder_agent.utils.intent import classify_message

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

    message_kind = classify_message(text)
    log.info("MSG kind=%s chat_id=%s", message_kind, chat_id)

    if message_kind == "report":
        await graph.ainvoke(
            {
                "chat_id": chat_id,
                "raw_report_text": text,
                "extracted_tasks": [],
                "tool_call_rounds": 0,
                "pending_updates": [],
            },
            config=_thread_config(chat_id),
        )
    else:
        # raw_report_text phải là None tường minh: giá trị cũ còn nằm trong
        # checkpoint sẽ khiến route_entry lôi câu hỏi sang nhánh trích xuất.
        # pending_updates cũng vậy: sót lại từ lượt trước là lượt này vừa trả
        # lời xong đã hỏi duyệt một đề xuất người dùng không hề nhắc tới.
        await graph.ainvoke(
            {
                "chat_id": chat_id,
                "raw_report_text": None,
                "messages": [HumanMessage(content=text)],
                "tool_call_rounds": 0,
                "pending_updates": [],
            },
            config=_thread_config(chat_id),
        )


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
    if update.message and update.message.text:
        await handle_text_message(graph, update.message)

    return {"ok": True}
