import asyncio
from datetime import date
from typing import Literal

from langchain_core.tools import tool

from app.core.datetime_utils import now_local, weekday_vi
from app.core.task_text import normalize_content
from persistence.models.task import Task
from persistence.proc.tasks import find_tasks_by_reference


def _brief(task: Task) -> dict:
    today = now_local().date()
    return {
        "task_id": task.task_id,
        "code": task.code,
        "group": task.group,
        "content": normalize_content(task.content),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_weekday": weekday_vi(task.due_date) if task.due_date else None,
        "days_left": (task.due_date - today).days if task.due_date else None,
    }


def _parse_due(new_due_date: str | None) -> date | None:
    try:
        return date.fromisoformat(new_due_date) if new_due_date else None
    except ValueError:
        return None


@tool
async def propose_task_update(
    task_ref: str,
    action: Literal["done", "cancel", "reschedule"],
    new_due_date: str | None = None,
) -> dict:
    """Đề xuất cập nhật một đầu việc. KHÔNG ghi dữ liệu — người dùng sẽ xác nhận bằng tin nhắn.

    Args:
        task_ref: mã việc (ví dụ "TB-002") hoặc một phần nội dung/tên nhóm.
        action: "done" khi người dùng báo đã xong, "cancel" khi bỏ việc,
            "reschedule" khi đổi hạn.
        new_due_date: hạn mới dạng "YYYY-MM-DD", bắt buộc khi action="reschedule".
    """
    parsed_due = _parse_due(new_due_date)
    if action == "reschedule" and parsed_due is None:
        # Hỏi lại chứ không đoán: đoán sai một cái hạn là người dùng trễ việc.
        return {
            "status": "invalid_due_date",
            "hint": 'Cần hạn mới dạng "YYYY-MM-DD". Hỏi lại người dùng.',
        }

    matches = await asyncio.to_thread(find_tasks_by_reference, task_ref)
    if not matches:
        return {"status": "not_found", "task_ref": task_ref}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "candidates": [_brief(task) for task in matches],
            "hint": "Đọc mã của từng ứng viên và hỏi người dùng chọn cái nào.",
        }

    task = matches[0]
    return {
        "status": "proposed",
        "action": action,
        "task_id": task.task_id,
        "code": task.code,
        "content": normalize_content(task.content),
        "new_due_date": parsed_due.isoformat() if parsed_due else None,
        "hint": "Chưa ghi gì cả. Người dùng sẽ được hỏi xác nhận ngay sau câu trả lời của bạn.",
    }
