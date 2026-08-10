import logging

from aiogram.types import Message, Update
from fastapi import APIRouter, Header, HTTPException, Request
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.core.security import verify_webhook_secret
from app.services.callback_service import handle_task_callback
from app.telegram.bot import bot
from app.telegram.messages import need_reason_text
from app.telegram.sender import send_message
from reminder_agent.utils.intent import classify_message, parse_free_text_decision

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks")


def _config(chat_id: int) -> dict:
    return {"configurable": {"thread_id": str(chat_id)}}


async def _is_awaiting_confirm(graph, chat_id: int) -> bool:
    snapshot = await graph.aget_state(_config(chat_id))
    return bool(snapshot.next) and "ask_confirm" in snapshot.next


async def _resume(graph, chat_id: int, status: str, edit_request: str | None) -> None:
    await graph.ainvoke(
        Command(resume={"status": status, "edit_request": edit_request}),
        config=_config(chat_id),
    )


async def handle_text_message(graph, message: Message) -> None:
    """Job C: tin nhắn text. Ưu tiên luồng xác nhận đang treo, rồi mới phân loại."""
    chat_id = message.chat.id
    text = message.text

    if await _is_awaiting_confirm(graph, chat_id):
        decision = await parse_free_text_decision(text)
        log.info("CONFIRM status=%s chat_id=%s", decision["status"], chat_id)
        # Không rõ ý, hoặc muốn sửa mà chưa nói sửa chỗ nào -> hỏi lại, đừng resume
        # (trích lại mà không biết sửa gì thì chỉ ra đúng kết quả cũ)
        if decision["status"] == "unclear" or (
            decision["status"] == "edit" and not decision["edit_request"]
        ):
            await send_message(chat_id, need_reason_text())
            return
        await _resume(graph, chat_id, decision["status"], decision.get("edit_request"))
        return

    kind = classify_message(text)
    log.info("MSG kind=%s chat_id=%s", kind, chat_id)

    if kind == "report":
        await graph.ainvoke(
            {
                "chat_id": chat_id,
                "raw_report_text": text,
                "extracted_tasks": [],
                "tool_call_rounds": 0,
            },
            config=_config(chat_id),
        )
    else:
        await graph.ainvoke(
            {
                "chat_id": chat_id,
                "raw_report_text": None,
                "messages": [HumanMessage(content=text)],
                "tool_call_rounds": 0,
            },
            config=_config(chat_id),
        )


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not verify_webhook_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # gắn bot vào context để cb.answer()/message.answer() dùng được
    update = Update.model_validate(await request.json(), context={"bot": bot})
    graph = request.app.state.graph

    # Chỉ còn nút của tin nhắn nhắc việc (task:done/snooze/undo). Bảng đầu việc
    # không có nút, người dùng duyệt/sửa bằng tin nhắn thường.
    if update.callback_query and update.callback_query.data:
        if update.callback_query.data.startswith("task:"):
            await handle_task_callback(update.callback_query)
        return {"ok": True}

    if update.message and update.message.text:
        await handle_text_message(graph, update.message)

    return {"ok": True}
