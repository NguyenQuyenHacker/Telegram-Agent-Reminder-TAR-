from datetime import datetime, timedelta

from app.core.config import settings
from app.core.datetime_utils import (
    is_due_within_days,
    is_overdue_by_days,
    next_window_start,
    to_local,
)
from persistence.models.task import Priority, Task, TaskStatus

_INTERVAL_TABLE = {
    (Priority.urgent, TaskStatus.pending): "urgent_pending_interval_min",
    (Priority.urgent, TaskStatus.snoozed): "urgent_snoozed_interval_min",
    (Priority.normal, TaskStatus.pending): "normal_pending_interval_min",
    (Priority.normal, TaskStatus.snoozed): "normal_snoozed_interval_min",
}


def interval_for(priority: Priority, state: TaskStatus) -> int:
    """R3: bảng nhịp nhắc, đọc từ cấu hình chứ không hard-code."""
    # Chỉ snoozed có nhịp riêng, mọi trạng thái còn lại dùng nhịp của pending
    normalized = state if state == TaskStatus.snoozed else TaskStatus.pending
    return getattr(settings, _INTERVAL_TABLE[priority, normalized])


def maybe_escalate(task: Task, reference_dt: datetime) -> Priority:
    """R4: việc thường được nâng lên ưu tiên khi quá hạn, hoặc khi sắp tới hạn.

    Ba nguồn dẫn tới urgent — gốc đã urgent, sắp tới hạn, đã quá hạn — đều ra
    cùng một mức. Nguyên nhân không lưu lại ở đây; tin nhắn nhắc tự nói ra qua
    dòng hạn ("Còn 2 ngày" hay "Quá hạn 22 ngày").
    """
    if task.priority == Priority.urgent:
        return Priority.urgent
    if is_overdue_by_days(task.due_date, reference_dt, settings.escalation_overdue_days):
        return Priority.urgent
    if is_due_within_days(task.due_date, reference_dt, settings.escalation_due_soon_days):
        return Priority.urgent
    return Priority.normal


def is_within_send_window(dt: datetime) -> bool:
    """R6: chỉ nhắc trong khung giờ đã cấu hình."""
    hour = to_local(dt).hour
    return settings.reminder_window_start_hour <= hour < settings.reminder_window_end_hour


def clamp_to_window(dt: datetime) -> datetime:
    """R6: mốc rơi ngoài khung giờ thì dồn về đầu khung ngày kế tiếp."""
    local = to_local(dt)
    if local.hour < settings.reminder_window_start_hour:
        return local.replace(
            hour=settings.reminder_window_start_hour, minute=0, second=0, microsecond=0
        )
    if local.hour >= settings.reminder_window_end_hour:
        return next_window_start(local)
    return local


def compute_next_remind_at(
    priority: Priority, state: TaskStatus, reference_dt: datetime
) -> datetime:
    """Mốc nhắc kế tiếp: cộng nhịp tương ứng rồi kéo về trong khung giờ."""
    raw = to_local(reference_dt) + timedelta(minutes=interval_for(priority, state))
    return clamp_to_window(raw)
