import asyncio
from datetime import date

from langchain_core.tools import tool

from app.core.datetime_utils import now_local
from persistence.models.task import Priority, TaskStatus
from persistence.proc.tasks import query_tasks as _query_tasks


@tool
async def query_tasks(
    group: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    due_from: str | None = None,
    due_to: str | None = None,
) -> list[dict]:
    """Lọc đầu việc trong cơ sở dữ liệu.

    Args:
        group: tên nhóm dự án, ví dụ "App Trưởng thôn, trưởng bản".
        status: "pending", "done" hoặc "snoozed".
        priority: "urgent" hoặc "normal".
        due_from: hạn từ ngày, dạng "YYYY-MM-DD".
        due_to: hạn đến ngày, dạng "YYYY-MM-DD".
    """
    tasks = await asyncio.to_thread(
        _query_tasks,
        group,
        TaskStatus(status) if status else None,
        Priority(priority) if priority else None,
        date.fromisoformat(due_from) if due_from else None,
        date.fromisoformat(due_to) if due_to else None,
    )
    today = now_local().date()
    return [
        {
            "task_id": t.task_id,
            "group": t.group,
            "content": t.content,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "days_left": (t.due_date - today).days if t.due_date else None,
            "priority": t.priority.value,
            "status": t.status.value,
        }
        for t in tasks
    ]
