from datetime import datetime, timedelta

from app.core.config import settings
from app.core.datetime_utils import (
    is_due_within_days,
    is_overdue_by_days,
    next_window_start,
    to_local,
)
from persistence.models.task import Priority, Task


def maybe_escalate(task: Task, reference_dt: datetime) -> Priority:
    """R4: việc thường được nâng lên ưu tiên khi quá hạn, hoặc khi sắp tới hạn.

    Ba nguồn dẫn tới urgent — gốc đã urgent, sắp tới hạn, đã quá hạn — đều ra
    cùng một mức. Nguyên nhân không lưu lại ở đây; tin nhắn nhắc tự nói ra qua
    dòng hạn ("Còn 2 ngày" hay "Quá hạn 22 ngày").

    Mức ưu tiên không còn ảnh hưởng nhịp nhắc (chỉ còn một nhịp duy nhất), nó
    quyết định việc nằm rổ nào trong tin nhắc gộp.
    """
    if task.priority == Priority.urgent:
        return Priority.urgent
    if is_overdue_by_days(task.due_date, reference_dt, settings.escalation_overdue_days):
        return Priority.urgent
    if is_due_within_days(task.due_date, reference_dt, settings.escalation_due_soon_days):
        return Priority.urgent
    return Priority.normal


def is_within_send_window(moment: datetime) -> bool:
    """R6: chỉ nhắc trong khung giờ đã cấu hình."""
    hour = to_local(moment).hour
    return settings.reminder_window_start_hour <= hour < settings.reminder_window_end_hour


def clamp_to_window(moment: datetime) -> datetime:
    """R6: mốc rơi ngoài khung giờ thì dồn về đầu khung ngày kế tiếp."""
    local_moment = to_local(moment)
    if local_moment.hour < settings.reminder_window_start_hour:
        return local_moment.replace(
            hour=settings.reminder_window_start_hour, minute=0, second=0, microsecond=0
        )
    if local_moment.hour >= settings.reminder_window_end_hour:
        return next_window_start(local_moment)
    return local_moment


def compute_next_remind_at(reference_dt: datetime) -> datetime:
    """R3: mốc nhắc kế tiếp — cộng nhịp cấu hình rồi kéo về trong khung giờ."""
    unclamped = to_local(reference_dt) + timedelta(minutes=settings.pending_interval_min)
    return clamp_to_window(unclamped)
