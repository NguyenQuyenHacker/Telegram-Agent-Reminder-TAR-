import asyncio
from typing import Literal

from langchain_core.tools import tool

from app.core.datetime_utils import now_local
from app.core.task_text import normalize_content
from persistence.proc.tasks import find_tasks_by_reference
from reminder_agent.utils.tools.task_view import task_brief


def _resolve(ref: str) -> dict | list:
    """Một tham chiếu -> đúng một Task, hoặc dict lỗi để trả thẳng cho LLM."""
    matches = find_tasks_by_reference(ref)
    if not matches:
        return {"status": "not_found", "task_ref": ref}
    if len(matches) > 1:
        now = now_local()
        return {
            "status": "ambiguous",
            "candidates": [task_brief(task, now) for task in matches],
            "hint": "Đọc mã của từng ứng viên và hỏi người dùng chọn cái nào.",
        }
    return matches


@tool
async def propose_subtask_update(
    action: Literal["add", "rename"],
    content: str,
    parent_ref: str | None = None,
    subtask_ref: str | None = None,
) -> dict:
    """Đề xuất thêm một việc con vào đầu việc, hoặc sửa tên một việc con đang có.

    KHÔNG ghi dữ liệu — người dùng sẽ xác nhận bằng tin nhắn. Mỗi việc con là
    MỘT lời gọi: chia một đầu việc thành bốn đầu mục thì gọi tool này bốn lần.

    Việc con có mã là mã việc lớn cộng thêm ".N", ví dụ "TB-002.1". Báo xong,
    hủy (tức là xóa nó khỏi danh sách) hay đổi hạn một việc con thì KHÔNG dùng
    tool này — dùng propose_task_update với mã việc con, vì việc con cũng là một
    đầu việc bình thường.

    Args:
        action: "add" để thêm việc con mới, "rename" để sửa nội dung việc con.
        content: nội dung việc con — cái cần thêm, hoặc tên mới khi sửa.
        parent_ref: mã hoặc một phần nội dung của việc LỚN. Bắt buộc khi action="add".
        subtask_ref: mã việc con (ví dụ "TB-002.1") hoặc một phần nội dung của nó.
            Bắt buộc khi action="rename".
    """
    clean_content = normalize_content(content or "")
    if not clean_content:
        return {
            "status": "missing_content",
            "hint": "Cần nội dung việc con. Hỏi lại người dùng cần thêm/sửa thành gì.",
        }

    ref = parent_ref if action == "add" else subtask_ref
    if not ref:
        needed = "parent_ref (việc lớn)" if action == "add" else "subtask_ref (việc con)"
        return {
            "status": "missing_reference",
            "hint": f"Thiếu {needed}. Hỏi người dùng cho biết mã việc.",
        }

    resolved = await asyncio.to_thread(_resolve, ref)
    if isinstance(resolved, dict):
        return resolved
    task = resolved[0]

    if action == "add" and task.is_subtask:
        # Chỉ một tầng. Nói rõ cho LLM thay vì để add_subtask trả None sau khi
        # người dùng đã gật — lúc đó họ chỉ nhận được một câu "không ghi được".
        return {
            "status": "nested_not_allowed",
            "task_ref": ref,
            "hint": f'"{task.code}" đã là việc con, không gắn thêm việc con vào nó được. '
            "Hỏi người dùng gắn vào việc lớn nào.",
        }
    if action == "rename" and not task.is_subtask:
        return {
            "status": "not_a_subtask",
            "task_ref": ref,
            "hint": f'"{task.code}" là việc lớn, không phải việc con. '
            "Chỉ sửa được tên việc con.",
        }

    return {
        "status": "proposed",
        # Hai action ghi vào hai dòng khác nhau — "add" ghi vào con của task_id
        # này, "rename" ghi đè chính nó — nên tên action phải nói rõ, apply_updates
        # tra bảng theo đúng chuỗi này.
        "action": "add_subtask" if action == "add" else "rename_subtask",
        "task_id": task.task_id,
        "code": task.code,
        "content": task.content,
        "subtask_content": clean_content,
        "hint": "Chưa ghi gì cả. Người dùng sẽ được hỏi xác nhận ngay sau câu trả lời của bạn.",
    }
