from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings

TZ = ZoneInfo(settings.local_timezone)


def now_local() -> datetime:
    return datetime.now(TZ)


def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def next_morning_8am(from_dt: datetime) -> datetime:
    local = to_local(from_dt)
    return (local + timedelta(days=1)).replace(
        hour=settings.reminder_window_start_hour, minute=0, second=0, microsecond=0
    )


def is_overdue_by_days(due_date: date | None, reference_dt: datetime, days: int) -> bool:
    if due_date is None:
        return False
    return (to_local(reference_dt).date() - due_date).days >= days
