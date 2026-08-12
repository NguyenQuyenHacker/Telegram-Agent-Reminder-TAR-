import asyncio
from datetime import date
from typing import Literal

from langchain_core.tools import tool

from app.core.datetime_utils import now_local
from persistence.proc.tasks import find_tasks_by_reference
from reminder_agent.utils.tools.task_view import task_brief

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
    if action == "reschedule":
        new_due = _parse_due(new_due_date)
        if new_due is None:
            # Hỏi lại chứ không đoán: đoán sai một cái hạn là người dùng trễ việc.
            return {
                "status": "invalid_due_date",
                "hint": 'Cần hạn mới dạng "YYYY-MM-DD". Hỏi lại người dùng.',
            }
    else:
        # Ngày gửi kèm "done"/"cancel" không có chỗ dùng — hai hành động đó không
        # đụng tới cột due_date. Bỏ ngay tại đây để nó không lọt vào đề xuất rồi
        # bảng xác nhận đọc lên một cái hạn sẽ không bao giờ được ghi.
        new_due = None

    matches = await asyncio.to_thread(find_tasks_by_reference, task_ref)
    if not matches:
        return {"status": "not_found", "task_ref": task_ref}
    if len(matches) > 1:
        now = now_local()
        return {
            "status": "ambiguous",
            "candidates": [task_brief(task, now) for task in matches],
            "hint": "Đọc mã của từng ứng viên và hỏi người dùng chọn cái nào.",
        }

    task = matches[0]
    return {
        "status": "proposed",
        "action": action,
        # task_id chỉ để apply_updates ghi đúng dòng; LLM không cần nhắc tới nó,
        # người dùng đọc mã việc ở trường code.
        "task_id": task.task_id,
        "code": task.code,
        "content": task.content,
        "new_due_date": new_due.isoformat() if new_due else None,
        "hint": "Chưa ghi gì cả. Người dùng sẽ được hỏi xác nhận ngay sau câu trả lời của bạn.",
    }
