"""Chia lô việc tới hạn thành các rổ ưu tiên của một tin nhắc gộp.

Hàm thuần, không I/O: Job A đọc DB rồi đưa danh sách vào đây, tầng soạn tin đọc
kết quả ra. Nhờ vậy quy tắc "việc nào nằm rổ nào" test được mà không cần DB.
"""

from datetime import datetime

from app.core.datetime_utils import to_local
from app.core.priority import maybe_escalate
from persistence.models.task import Priority, Task

OVERDUE = "overdue"
URGENT = "urgent"
NORMAL = "normal"

# Thứ tự hiển thị trong tin nhắn, cũng là thứ tự đọc: quá hạn đập vào mắt trước.
BUCKET_ORDER = (OVERDUE, URGENT, NORMAL)


def group_by_bucket(tasks: list[Task], now: datetime) -> dict[str, list[Task]]:
    """Ba rổ, luôn đủ ba khoá kể cả khi rỗng.

    Quá hạn tách riêng khỏi ưu tiên dù maybe_escalate cũng nâng việc quá hạn lên
    urgent: người dùng cần phân biệt "gấp" với "đã trễ", hai thứ cần hành động
    khác nhau. Việc chưa có hạn rơi vào rổ thường.
    """
    buckets: dict[str, list[Task]] = {name: [] for name in BUCKET_ORDER}
    today = to_local(now).date()
    for task in tasks:
        if task.due_date is not None and task.due_date < today:
            buckets[OVERDUE].append(task)
        elif maybe_escalate(task, now) == Priority.urgent:
            buckets[URGENT].append(task)
        else:
            buckets[NORMAL].append(task)
    return buckets
