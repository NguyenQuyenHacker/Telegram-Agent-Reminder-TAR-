import asyncio
import logging

from app.core.config import settings
from app.core.datetime_utils import next_morning_8am, now_local
from app.core.priority import (
    compute_next_remind_at,
    interval_for,
    is_within_send_window,
    maybe_escalate,
)
from app.telegram.keyboards import reminder_keyboard
from app.telegram.messages import reminder_text
from app.telegram.sender import send_message
from persistence.models.task import TaskStatus
from persistence.proc.tasks import get_due_tasks, reschedule_task

log = logging.getLogger(__name__)


async def run_job_a() -> None:
    """Job A: quét việc tới hạn và chủ động nhắc. Không dùng AI, không phải graph."""
    if settings.telegram_chat_id is None:
        log.warning("Chưa cấu hình TELEGRAM_CHAT_ID, bỏ qua lượt quét")
        return

    now = now_local()
    due = await asyncio.to_thread(get_due_tasks, now)
    if not due:
        return

    log.info("SCAN: %d việc tới hạn", len(due))

    for task in due:
        # R6: ngoài khung giờ thì dồn sang đầu khung ngày kế tiếp, không gửi
        if not is_within_send_window(now):
            await asyncio.to_thread(
                reschedule_task, task.task_id, next_morning_8am(now), task.last_reminded_at or now
            )
            continue

        priority = maybe_escalate(task, now)
        state = TaskStatus.snoozed if task.status == TaskStatus.snoozed else TaskStatus.pending

        log.info("DUE task_id=%s priority=%s -> gửi nhắc", task.task_id, priority.value)
        try:
            await send_message(
                settings.telegram_chat_id,
                reminder_text(task, now, priority),
                reminder_keyboard(task.task_id),
            )
        except Exception:
            log.exception("Gửi nhắc thất bại task_id=%s, để lượt sau thử lại", task.task_id)
            continue

        await asyncio.to_thread(
            reschedule_task,
            task.task_id,
            compute_next_remind_at(priority, state, now),
            now,
            priority,
            interval_for(priority, state),
        )
