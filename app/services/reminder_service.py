import asyncio
import logging
from datetime import datetime

from app.core.config import settings
from app.core.datetime_utils import next_window_start, now_local
from app.core.priority import compute_next_remind_at, is_within_send_window
from app.core.reminder_digest import group_by_bucket
from app.telegram.messages import REMINDER_PARSE_MODE, digest_text
from app.telegram.sender import send_message
from persistence.models.task import Task
from persistence.proc.subtasks import subtask_progress
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


async def _reschedule_after_digest(task: Task, now: datetime) -> None:
    """Hẹn mốc kế tiếp sau khi tin gộp đã gửi xong.

    CỐ Ý không ghi maybe_escalate() xuống DB. Mức ưu tiên nâng do quá hạn là
    trạng thái phái sinh từ due_date, ghi đè nó lên cột priority là mất mức ưu
    tiên gốc của báo cáo — và vì maybe_escalate() trả urgent ngay khi thấy cột
    đã urgent, việc đó thành một chiều: dời hạn ra tương lai cũng không hạ lại
    được. Rổ hiển thị đã tính maybe_escalate() mỗi lượt gửi rồi.
    """
    await asyncio.to_thread(
        reschedule_task,
        task.task_id,
        compute_next_remind_at(now),
        now,
    )


async def run_job_a() -> None:
    """Job A: quét việc tới hạn và chủ động nhắc. Không dùng AI, không phải graph.

    Cả lô đi trong MỘT tin nhắn: mỗi việc một tin thì đến lượt quét đông việc là
    người dùng nhận cả tràng thông báo, đọc không nổi và tắt luôn bot.
    """
    if settings.telegram_chat_id is None:
        log.warning("Chưa cấu hình TELEGRAM_CHAT_ID, bỏ qua lượt quét")
        return

    now = now_local()
    due_tasks = await asyncio.to_thread(get_due_tasks, now)
    if not due_tasks:
        return

    log.info("SCAN: %d việc tới hạn", len(due_tasks))

    if not is_within_send_window(now):
        for task in due_tasks:
            await _defer_until_next_window(task, now)
        return

    # Một truy vấn cho cả lô, không phải một truy vấn cho mỗi dòng. Việc chưa
    # chia nhỏ thì không có khoá trong dict và tin nhắn không in dòng tiến độ.
    progress = await asyncio.to_thread(
        subtask_progress, [task.task_id for task in due_tasks]
    )

    try:
        await send_message(
            settings.telegram_chat_id,
            digest_text(group_by_bucket(due_tasks, now), now, progress),
            parse_mode=REMINDER_PARSE_MODE,
        )
    except Exception:
        # Không đụng next_remind_at: lượt quét sau gặp lại đúng lô này và thử lại.
        log.exception("Gửi tin nhắc gộp thất bại, để lượt sau thử lại")
        return

    for task in due_tasks:
        await _reschedule_after_digest(task, now)
