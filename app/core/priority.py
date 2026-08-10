from datetime import datetime

from app.core.config import settings
from app.core.datetime_utils import is_overdue_by_days, next_morning_8am, to_local
from persistence.models.task import Priority, Task, TaskStatus

_INTERVAL_TABLE = {
    (Priority.urgent, TaskStatus.pending): "urgent_pending_interval_min",
    (Priority.urgent, TaskStatus.snoozed): "urgent_snoozed_interval_min",
    (Priority.normal, TaskStatus.pending): "normal_pending_interval_min",
    (Priority.normal, TaskStatus.snoozed): "normal_snoozed_interval_min",
}


def interval_for(priority: Priority, state: TaskStatus) -> int:
    """R3: bảng nhịp nhắc, đọc từ cấu hình chứ không hard-code."""
    key = (priority, TaskStatus.snoozed if state == TaskStatus.snoozed else TaskStatus.pending)
    return getattr(settings, _INTERVAL_TABLE[key])


def maybe_escalate(task: Task, reference_dt: datetime) -> Priority:
    """R4: việc thường quá hạn từ 1 ngày trở lên thì nâng lên ưu tiên."""
    if task.priority == Priority.urgent:
        return Priority.urgent
    if is_overdue_by_days(task.due_date, reference_dt, settings.escalation_overdue_days):
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
        return next_morning_8am(local)
    return local


def compute_next_remind_at(
    priority: Priority, state: TaskStatus, reference_dt: datetime
) -> datetime:
    from datetime import timedelta

    raw = to_local(reference_dt) + timedelta(minutes=interval_for(priority, state))
    return clamp_to_window(raw)
