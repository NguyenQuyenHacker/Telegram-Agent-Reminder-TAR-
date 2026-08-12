"""Một dòng Task -> dict cho LLM đọc.

Hai tool dùng chung đúng một hình dạng: LLM khỏi học hai bộ tên trường, và cái
nó kể lại cho người dùng khớp với tin nhắc gộp của Job A.
"""

from datetime import datetime

from app.core.datetime_utils import to_local, weekday_vi
from app.core.priority import maybe_escalate
from persistence.models.task import Task


def task_brief(task: Task, now: datetime) -> dict:
    """Bản rút gọn của một đầu việc.

    priority là mức HIỆU LỰC (đã qua maybe_escalate) chứ không phải giá trị thô
    trong cột: việc quá hạn hoặc sắp tới hạn được tin nhắc gộp xếp vào rổ ưu
    tiên, trả cột thô ở đây là bot nói "bình thường" về đúng việc nó vừa nhắc đỏ.

    due_weekday và days_left tính sẵn bằng Python: suy lịch là thứ LLM làm sai
    thường xuyên, mà sai kiểu này thì trông vẫn rất thật.
    """
    today = to_local(now).date()
    return {
        # code là thứ người dùng gõ lại được, task_id thì không
        "code": task.code,
        "group": task.group_name,
        "content": task.content,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_weekday": weekday_vi(task.due_date) if task.due_date else None,
        "days_left": (task.due_date - today).days if task.due_date else None,
        "priority": maybe_escalate(task, now).value,
        "status": task.status.value,
    }
