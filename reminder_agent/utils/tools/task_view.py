"""Một dòng Task -> dict cho LLM đọc.

Hai tool dùng chung đúng một hình dạng: LLM khỏi học hai bộ tên trường, và cái
nó kể lại cho người dùng khớp với tin nhắc gộp của Job A.
"""

from datetime import datetime

from app.core.datetime_utils import to_local, weekday_vi
from app.core.priority import maybe_escalate
from persistence.models.task import Task


def task_brief(
    task: Task, now: datetime, progress: tuple[int, int] | None = None
) -> dict:
    """Bản rút gọn của một đầu việc.

    priority là mức HIỆU LỰC (đã qua maybe_escalate) chứ không phải giá trị thô
    trong cột: việc quá hạn hoặc sắp tới hạn được tin nhắc gộp xếp vào rổ ưu
    tiên, trả cột thô ở đây là bot nói "bình thường" về đúng việc nó vừa nhắc đỏ.

    due_weekday và days_left tính sẵn bằng Python: suy lịch là thứ LLM làm sai
    thường xuyên, mà sai kiểu này thì trông vẫn rất thật.

    `progress` là (đã xong, tổng) việc con, do nơi gọi đọc cả lô một lượt rồi
    đưa vào — hàm này không đụng DB. Không có việc con thì trường `progress` là
    null, khác hẳn với {"done": 0, ...}: một bên là chưa chia nhỏ, một bên là
    chia rồi mà chưa xong cái nào.
    """
    today = to_local(now).date()
    done_total = (
        {"done": progress[0], "total": progress[1]} if progress is not None else None
    )
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
        # Để LLM biết dòng này là một đầu mục bên trong việc khác, đừng kể nó ra
        # ngang hàng với việc lớn.
        "is_subtask": task.is_subtask,
        "progress": done_total,
    }
