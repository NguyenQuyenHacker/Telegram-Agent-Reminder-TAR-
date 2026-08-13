import asyncio

from langchain_core.tools import tool

from app.core.datetime_utils import now_local
from persistence.models.task import TaskStatus
from persistence.proc.subtasks import get_subtasks
from persistence.proc.tasks import find_tasks_by_reference
from reminder_agent.utils.tools.task_view import task_brief

# Xem lại thì xem được cả việc đã chốt — khác với propose_task_update, nơi chỉ
# việc đang chờ mới nhận lệnh. "Tuần trước tôi xong mấy đầu mục của TB-002 rồi?"
# là câu hỏi hợp lệ.
_READABLE_STATUSES = tuple(TaskStatus)


@tool
async def get_task_detail(task_ref: str) -> dict:
    """Xem chi tiết một đầu việc: hạn, danh sách việc con, và tiến độ.

    Dùng khi người dùng hỏi tiến độ, hỏi một việc lớn gồm những đầu mục nào,
    hoặc muốn xem chi tiết một việc. KHÔNG ghi dữ liệu.

    Args:
        task_ref: mã việc (ví dụ "TB-002") hoặc một phần nội dung/tên nhóm.
    """
    matches = await asyncio.to_thread(
        find_tasks_by_reference, task_ref, statuses=_READABLE_STATUSES
    )
    now = now_local()
    if not matches:
        return {"status": "not_found", "task_ref": task_ref}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "candidates": [task_brief(task, now) for task in matches],
            "hint": "Đọc mã của từng ứng viên và hỏi người dùng chọn cái nào.",
        }

    task = matches[0]
    subtasks = await asyncio.to_thread(get_subtasks, task.task_id)
    done_count = sum(1 for subtask in subtasks if subtask.status == TaskStatus.done)
    return {
        "status": "detail",
        "task": task_brief(task, now, (done_count, len(subtasks))),
        "subtasks": [task_brief(subtask, now) for subtask in subtasks],
        "progress": {"done": done_count, "total": len(subtasks)},
        # Cùng lý do với propose_task_update: bảng do code dựng mới là bản
        # chuẩn, LLM kể lại là hai tin nhắn nói cùng một chuyện, bản sau sai số.
        "hint": "Hệ thống đã gửi bảng chi tiết cho người dùng. Đừng kể lại nội dung đó.",
    }
