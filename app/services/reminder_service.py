import asyncio
import logging
from datetime import datetime

from app.core.config import settings
from app.core.datetime_utils import next_window_start, now_local
from app.core.priority import (
    compute_next_remind_at,
    interval_for,
    is_within_send_window,
    maybe_escalate,
)
from app.telegram.keyboards import reminder_keyboard
from app.telegram.messages import REMINDER_PARSE_MODE, reminder_text
from app.telegram.sender import send_message
from persistence.models.task import Task, TaskStatus
from persistence.proc.tasks import get_due_tasks, reschedule_task

log = logging.getLogger(__name__)


async def _defer_until_next_window(task: Task, now: datetime) -> None:
    """R6: ngoài khung giờ thì dời sang đầu khung ngày kế tiếp, không gửi gì."""
    await asyncio.to_thread(
        reschedule_task,
        task.task_id,
        next_window_start(now),
        task.last_reminded_at or now,
    )


async def _send_and_reschedule(task: Task, now: datetime) -> None:
    """Gửi một tin nhắn nhắc rồi hẹn mốc kế tiếp theo nhịp tương ứng.

    Gửi lỗi thì để nguyên next_remind_at, lượt quét sau sẽ gặp lại việc này.
    """
    priority = maybe_escalate(task, now)
    remind_status = (
        TaskStatus.snoozed if task.status == TaskStatus.snoozed else TaskStatus.pending
    )

    log.info("DUE task_id=%s priority=%s -> gửi nhắc", task.task_id, priority.value)
    try:
        await send_message(
            settings.telegram_chat_id,
            reminder_text(task, now, priority),
            reminder_keyboard(task.task_id),
            parse_mode=REMINDER_PARSE_MODE,
        )
    except Exception:
        log.exception("Gửi nhắc thất bại task_id=%s, để lượt sau thử lại", task.task_id)
        return

    await asyncio.to_thread(
        reschedule_task,
        task.task_id,
        compute_next_remind_at(priority, remind_status, now),
        now,
        priority,
        interval_for(priority, remind_status),
    )


async def run_job_a() -> None:
    """Job A: quét việc tới hạn và chủ động nhắc. Không dùng AI, không phải graph."""
    if settings.telegram_chat_id is None:
        log.warning("Chưa cấu hình TELEGRAM_CHAT_ID, bỏ qua lượt quét")
        return

    now = now_local()
    due_tasks = await asyncio.to_thread(get_due_tasks, now)
    if not due_tasks:
        return

    log.info("SCAN: %d việc tới hạn", len(due_tasks))
    within_window = is_within_send_window(now)

    for task in due_tasks:
        if within_window:
            await _send_and_reschedule(task, now)
        else:
            await _defer_until_next_window(task, now)
