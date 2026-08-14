"""Điểm vào của cả hai luồng.

Hai endpoint, hai bot, hai secret, hai graph — không dùng chung gì ngoài hàm
tiện ích. Ranh giới quyền nằm ở ĐÂY, sớm nhất có thể: một tin nhắn của người
không phải admin không được đi xa hơn dòng `is_admin()`.

  POST /webhooks/telegram/admin   -> handle_admin_message  -> graph_admin
  POST /webhooks/telegram/client  -> handle_client_message -> graph_client

Tầng này KHÔNG phân luồng nghiệp vụ. Nó chốt quyền, tải file nếu có, nhét vào
state rồi gọi graph — chuyện "file thì nạp, chữ thì trả lời" là việc của node
`route` bên trong graph_admin.

Việc nặng (tải file, đọc PDF, gọi embedding) KHÔNG được chạy trong lời gọi
webhook: giữ HTTP response 30 giây là Telegram tưởng hỏng và gửi lại update,
mình nạp file hai lần. Trả 200 ngay rồi xử lý nền — xem `_spawn`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.core.security import is_admin, verify_webhook_secret
from app.telegram import keyboard
from app.telegram.bots import ADMIN, CLIENT, BotRole
from app.telegram.download import UploadRejected, download_document
from app.telegram.render import render
from app.telegram.sender import send_message

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks")

# Trần số lượt nạp chạy song song. Không phải để giữ CPU mà để giữ pool kết nối
# Postgres: mỗi lượt nạp chiếm vài kết nối trong lúc ghi chunk.
_INGEST_SLOTS = asyncio.Semaphore(2)

# Mỗi chat một khoá: hai tin nhắn của cùng một người tới sát nhau mà cùng vào
# graph là hai lượt chạy đè lên một thread_id.
_chat_locks: dict[tuple[str, int], asyncio.Lock] = {}


def thread_config(role: BotRole, chat_id: int) -> dict[str, dict[str, str]]:
    """Mỗi (vai, chat) là một thread riêng của LangGraph.

    Có tiền tố vai vì cùng một người có thể nhắn cả hai bot: dùng chung
    thread_id là hội thoại admin và hội thoại client trộn vào nhau.
    """
    return {"configurable": {"thread_id": f"{role.name}:{chat_id}"}}


def chat_lock(role: BotRole, chat_id: int) -> asyncio.Lock:
    return _chat_locks.setdefault((role.name, chat_id), asyncio.Lock())


async def pending_interrupt(graph: Any, config: dict) -> dict | None:
    """Payload của `interrupt()` đang treo, hoặc None nếu graph đã chạy xong.

    Ở langgraph 0.2.60 (bản đang ghim), `ainvoke` KHÔNG trả khoá `__interrupt__`
    — tài liệu bản mới hơn nói có, bản này thì không. Điểm dừng chỉ thấy qua
    `aget_state()`. Nghĩa là nhìn kết quả `ainvoke` thì không phân biệt được
    "chạy xong" với "đang chờ admin bấm". Nâng cấp langgraph thì kiểm lại đây
    trước tiên.
    """
    snapshot = await graph.aget_state(config)
    for task in snapshot.tasks:
        for interrupt in task.interrupts:
            return interrupt.value
    return None


def _spawn(coro: Awaitable[None]) -> None:
    """Chạy nền, không giữ HTTP response. Lỗi thì log chứ không nuốt im lặng."""

    async def _guarded() -> None:
        try:
            await coro
        except Exception:
            log.exception("Xử lý nền thất bại")

    asyncio.create_task(_guarded())


# ----------------------------------------------------------------------
# Luồng admin — nạp và quản lý tài liệu
# ----------------------------------------------------------------------

# kind của interrupt -> (câu hỏi, cách dựng bàn phím). Thêm điểm dừng mới cho
# graph_admin thì thêm một dòng ở đây, không phải một nhánh if.
_PROMPTS: dict[str, tuple[str, Callable[[dict], InlineKeyboardMarkup]]] = {
    "choose_project": (
        "Nạp {file_name} vào dự án nào?",
        lambda data: keyboard.choose_project_keyboard(data["projects"]),
    ),
    "confirm_overwrite": (
        "{file_name} đã có trong {project_name} ({existing_chunk_count} đoạn, "
        "nạp {existing_uploaded_at}).\nGhi đè bằng bản mới?",
        lambda data: keyboard.confirm_overwrite_keyboard(),
    ),
}


async def _send(
    chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
) -> bool:
    """Gửi cho admin, trả về gửi được hay không. Telegram hỏng thì log, KHÔNG
    ném tiếp.

    Hàm này hay được gọi từ trong `except`; để nó ném ra nữa là lỗi gốc bị thay
    bằng lỗi gửi tin, log mất dấu chuyện thật sự đã xảy ra.
    """
    try:
        await send_message(ADMIN.bot, chat_id, text, reply_markup=markup)
        return True
    except Exception:
        log.exception("Không gửi được tin cho admin (chat %s)", chat_id)
        return False


async def _tell(chat_id: int, event: dict) -> None:
    await _send(chat_id, render(event))


async def _tell_broken(chat_id: int) -> None:
    await _tell(chat_id, {"kind": "system_error", "data": {}})


async def _ask(chat_id: int, payload: dict) -> None:
    """Gửi câu hỏi kèm bàn phím của một interrupt đang treo."""
    kind = payload.get("kind", "")
    data = payload.get("data", {})
    prompt = _PROMPTS.get(kind)
    if prompt is None:
        log.error("interrupt lạ, không dựng được bàn phím: %r", kind)
        return

    template, build_keyboard = prompt
    try:
        text = template.format(**data)
        markup = build_keyboard(data)
    except (KeyError, TypeError):
        log.exception("Payload interrupt %r thiếu dữ liệu để dựng bàn phím", kind)
        await _tell_broken(chat_id)
        return

    # Graph ĐANG DỪNG chờ admin bấm; bàn phím không tới nơi -> lượt nạp treo
    # vĩnh viễn ở đó. Báo để admin biết mà gửi lại file.
    if not await _send(chat_id, text, markup):
        await _tell_broken(chat_id)


async def _reply(graph: Any, config: dict, chat_id: int, out: dict) -> None:
    """Sau một lượt ainvoke: dừng chờ bấm thì gửi bàn phím, xong thì đọc outbox.

    THỨ TỰ bắt buộc: kiểm interrupt TRƯỚC. `outbox` không có reducer riêng nên
    mỗi lượt `report` GHI ĐÈ nó — đọc `out["outbox"]` khi graph đang DỪNG ở một
    interrupt (report chưa chạy) là đọc phải giá trị của lượt TRƯỚC trên cùng
    thread_id, gửi nhầm tin cũ cho một câu hỏi mới.
    """
    payload = await pending_interrupt(graph, config)
    if payload:
        await _ask(chat_id, payload)
        return
    for event in out.get("outbox", []):
        await _tell(chat_id, event)


async def _run_admin_turn(app: FastAPI, chat_id: int, payload: Any) -> None:
    """Một lượt chạy graph_admin: khoá theo chat, invoke, rồi trả lời.

    `payload` là state của lượt mới, hoặc `Command(resume=...)` khi admin vừa
    bấm nút — với graph thì hai thứ đó đi cùng một đường.
    """
    try:
        async with chat_lock(ADMIN, chat_id), _INGEST_SLOTS:
            graph = app.state.admin_graph
            config = thread_config(ADMIN, chat_id)
            out = await graph.ainvoke(payload, config)
            await _reply(graph, config, chat_id, out)
    except Exception:
        # Hỏng ở TẦNG NGOÀI graph: mất kết nối Neon, checkpoint không đọc được.
        # Không bắt ở đây thì aiogram nuốt, ghi log, rồi thôi — admin gửi file
        # xong ngồi chờ mãi. Từ phía admin, "bot chết" và "bot đang đọc file 40
        # trang" trông y hệt nhau. Với lượt bấm nút còn tệ hơn: bàn phím đã bị
        # gỡ nên không bấm lại được.
        log.exception("Lượt admin thất bại (chat %s)", chat_id)
        await _tell_broken(chat_id)


async def handle_admin_message(app: FastAPI, message: Message) -> None:
    """Chốt quyền, tải file nếu có, đẩy vào graph. Không quyết định gì thêm —
    "file thì nạp, chữ thì trả lời" là việc của node `route` trong graph_admin.
    """
    if not is_admin(message.from_user.id if message.from_user else None):
        log.warning("Từ chối user %s ở bot admin", message.from_user)
        return

    chat_id = message.chat.id
    try:
        upload = (
            await download_document(ADMIN.bot, message) if message.document else None
        )
    except UploadRejected as exc:
        await _tell(
            chat_id,
            {"kind": "upload_rejected", "data": {"reason": exc.reason, **exc.data}},
        )
        return
    except Exception:
        log.exception("Tải file thất bại ngoài dự tính")
        await _tell_broken(chat_id)
        return

    await _run_admin_turn(
        app,
        chat_id,
        {
            "messages": [HumanMessage(message.text or message.caption or "")],
            "upload": upload,
            "uploaded_by": message.from_user.id,
        },
    )


async def handle_admin_callback(app: FastAPI, callback: CallbackQuery) -> None:
    """Admin bấm nút bàn phím (chọn dự án / xác nhận ghi đè) -> resume graph."""
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer()
        return
    if callback.message is None or callback.data is None:
        await callback.answer()
        return

    try:
        resume = keyboard.resume_value(callback.data)
    except ValueError:
        log.warning("callback_data lạ từ admin: %r", callback.data)
        await callback.answer()
        return

    await callback.answer()
    with suppress(Exception):
        # Chặn bấm lại vào nút của một lượt đã xử lý xong.
        await callback.message.edit_reply_markup(reply_markup=None)

    await _run_admin_turn(app, callback.message.chat.id, Command(resume=resume))


# ----------------------------------------------------------------------
# Luồng client — hỏi đáp, KHÔNG có đường nào ghi vào kho
# ----------------------------------------------------------------------

async def handle_client_message(app: FastAPI, message: Message) -> None:
    """graph_client CHƯA xây (retrieve/generate còn NotImplementedError) — trả
    lời tạm chứ không gọi graph. Xoá nhánh tạm này khi graph_client xong.
    """
    text = (
        "Bot này chỉ trả lời câu hỏi, không nhận file."
        if message.document
        else "Tính năng hỏi đáp đang được xây dựng, chưa dùng được. Quay lại sau nhé."
    )
    await send_message(CLIENT.bot, message.chat.id, text)


async def handle_client_callback(app: FastAPI, callback: CallbackQuery) -> None:
    """graph_client chưa có interrupt nào -> chưa có bàn phím nào để bấm."""
    await callback.answer()


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------

# Mỗi vai một bộ handler. Tra bảng chứ không `if role is ADMIN` rải khắp nơi —
# ở đây và ở polling.py (dev local) đều dùng chung bảng này.
HANDLERS: dict[str, dict[str, Callable]] = {
    ADMIN.name: {"message": handle_admin_message, "callback": handle_admin_callback},
    CLIENT.name: {"message": handle_client_message, "callback": handle_client_callback},
}


async def _receive(request: Request, role: BotRole, secret_header: str | None) -> dict:
    if not verify_webhook_secret(secret_header, role.webhook_secret):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # gắn bot vào context để message.answer() dùng đúng con bot của vai này
    update = Update.model_validate(await request.json(), context={"bot": role.bot})
    handlers = HANDLERS[role.name]
    if update.message:
        _spawn(handlers["message"](request.app, update.message))
    elif update.callback_query:
        _spawn(handlers["callback"](request.app, update.callback_query))
    return {"ok": True}


@router.post(ADMIN.webhook_path.removeprefix("/webhooks"))
async def admin_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    return await _receive(request, ADMIN, x_telegram_bot_api_secret_token)


@router.post(CLIENT.webhook_path.removeprefix("/webhooks"))
async def client_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    return await _receive(request, CLIENT, x_telegram_bot_api_secret_token)
