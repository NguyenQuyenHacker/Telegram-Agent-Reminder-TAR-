import asyncio
import logging

from aiogram.types import CallbackQuery

from app.core.datetime_utils import now_local
from app.core.priority import compute_next_remind_at, interval_for, maybe_escalate
from app.telegram.keyboards import undo_keyboard
from app.telegram.messages import (
    done_confirmation_text,
    snooze_confirmation_text,
    undo_confirmation_text,
    undo_expired_text,
)
from app.telegram.sender import edit_message, send_message
from persistence.models.task import TaskStatus
from persistence.proc.tasks import get_task, mark_done, snooze_task, undo_done

log = logging.getLogger(__name__)


async def handle_task_callback(cb: CallbackQuery) -> None:
    """Job B: xử lý nút Đã xong / Nhắc sau / Hoàn tác. Không dùng AI, không phải graph.

    Thứ tự bắt buộc: ghi DB trước, sửa tin nhắn sau — để lỗi ghi không hiện
    trạng thái sai cho người dùng.

    Riêng cb.answer() chỉ tắt vòng xoay trên nút, không hiển thị trạng thái việc,
    nên gọi ngay từ đầu cho nhánh done/snooze: người dùng thấy nút nhả tức thì
    thay vì chờ hết 2 vòng round-trip DB + 1 vòng sửa tin nhắn.
    """
    _, action, task_id = cb.data.split(":", 2)
    log.info("CALLBACK action=%s task_id=%s", action, task_id)

    if action == "done":
        await cb.answer("Đã ghi nhận")
        # mark_done tự đọc task, không cần get_task riêng trước đó
        updated = await asyncio.to_thread(mark_done, task_id)
        if updated is None:
            await send_message(cb.message.chat.id, "Không tìm thấy việc này")
            return
        await edit_message(
            cb.message.chat.id,
            cb.message.message_id,
            done_confirmation_text(updated),
            undo_keyboard(task_id),
        )

    elif action == "snooze":
        await cb.answer("Sẽ nhắc lại")
        task = await asyncio.to_thread(get_task, task_id)
        if task is None:
            await send_message(cb.message.chat.id, "Không tìm thấy việc này")
            return
        now = now_local()
        priority = maybe_escalate(task, now)
        next_at = compute_next_remind_at(priority, TaskStatus.snoozed, now)
        updated = await asyncio.to_thread(
            snooze_task, task_id, next_at, interval_for(priority, TaskStatus.snoozed)
        )
        await edit_message(
            cb.message.chat.id,
            cb.message.message_id,
            snooze_confirmation_text(updated, next_at),
        )

    elif action == "undo":
        # Giữ nguyên answer-sau: nhánh này cần bật alert khi quá hạn hoàn tác
        updated = await asyncio.to_thread(undo_done, task_id)
        if updated is None:
            await cb.answer(undo_expired_text(), show_alert=True)
            return
        await edit_message(
            cb.message.chat.id, cb.message.message_id, undo_confirmation_text(updated)
        )
        await cb.answer("Đã hoàn tác")

    else:
        await cb.answer("Thao tác không hợp lệ")
