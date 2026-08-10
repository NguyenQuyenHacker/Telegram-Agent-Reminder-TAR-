from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import settings

TZ = ZoneInfo(settings.local_timezone)


def now_local() -> datetime:
    return datetime.now(TZ)


# date.weekday() trả 0 cho thứ Hai, khớp đúng thứ tự tuple này
_WEEKDAY_VI = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


def weekday_vi(d: date) -> str:
    """Tên thứ trong tuần.

    Tính ở Python rồi đưa sẵn cho LLM, không để nó tự suy từ ngày ISO: suy lịch
    là thứ LLM làm sai thường xuyên, mà sai kiểu này thì trông vẫn rất thật.
    """
    return _WEEKDAY_VI[d.weekday()]


def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def next_window_start(from_dt: datetime) -> datetime:
    """Đầu khung giờ nhắc của ngày hôm sau."""
    local = to_local(from_dt)
    return (local + timedelta(days=1)).replace(
        hour=settings.reminder_window_start_hour, minute=0, second=0, microsecond=0
    )


def is_overdue_by_days(due_date: date | None, reference_dt: datetime, days: int) -> bool:
    if due_date is None:
        return False
    return (to_local(reference_dt).date() - due_date).days >= days


def is_due_within_days(due_date: date | None, reference_dt: datetime, days: int) -> bool:
    """Còn tối đa `days` ngày nữa là tới hạn. Hạn đúng hôm nay cũng tính.

    Việc đã quá hạn trả False — đó là phần việc của is_overdue_by_days, để hai
    hàm không cùng nhận một mốc.
    """
    if due_date is None:
        return False
    return 0 <= (due_date - to_local(reference_dt).date()).days <= days
