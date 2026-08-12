import asyncio
from datetime import date

from langchain_core.tools import tool

from app.core.datetime_utils import now_local
from app.core.priority import maybe_escalate
from persistence.models.task import Priority, TaskStatus
from persistence.proc.tasks import query_tasks as _query_tasks
from reminder_agent.utils.tools.task_view import task_brief

_STATUS_VALUES = ", ".join(status.value for status in TaskStatus)
_PRIORITY_VALUES = ", ".join(priority.value for priority in Priority)


def _parse_status(value: str | None) -> TaskStatus | None:
    """Không đoán thay LLM: sai giá trị thì báo lỗi kèm danh sách hợp lệ.

    Lỗi ném ra ở đây được graph gói lại thành ToolMessage, nên LLM đọc được và
    gọi lại cho đúng — im lặng trả bảng rỗng thì nó tưởng là không có việc nào.
    """
    if not value:
        return None
    try:
        return TaskStatus(value.strip().lower())
    except ValueError:
        raise ValueError(
            f'status phải là một trong: {_STATUS_VALUES}. Nhận được "{value}".'
        ) from None


def _parse_priority(value: str | None) -> Priority | None:
    if not value:
        return None
    try:
        return Priority(value.strip().lower())
    except ValueError:
        raise ValueError(
            f'priority phải là một trong: {_PRIORITY_VALUES}. Nhận được "{value}".'
        ) from None


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(
            f'{field} phải là ngày dạng "YYYY-MM-DD". Nhận được "{value}".'
        ) from None


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
        status: "pending", "done" hoặc "cancelled".
        priority: "urgent" hoặc "normal".
        due_from: hạn từ ngày, dạng "YYYY-MM-DD".
        due_to: hạn đến ngày, dạng "YYYY-MM-DD".
    """
    wanted_status = _parse_status(status)
    wanted_priority = _parse_priority(priority)
    from_date = _parse_date(due_from, "due_from")
    to_date = _parse_date(due_to, "due_to")
    if from_date and to_date and from_date > to_date:
        # Khoảng ngược luôn ra rỗng; nói thẳng còn hơn để LLM báo "không có việc nào".
        raise ValueError(
            f"due_from ({from_date}) phải trước hoặc bằng due_to ({to_date})."
        )

    tasks = await asyncio.to_thread(
        _query_tasks, group, wanted_status, from_date, to_date
    )
    now = now_local()
    if wanted_priority:
        # Lọc theo mức HIỆU LỰC, cùng công thức với rổ trong tin nhắc gộp. Lọc
        # bằng SQL trên cột thô thì "việc nào đang gấp" rụng hết những việc được
        # nâng vì quá hạn. Đổi lại, trần _QUERY_RESULT_LIMIT áp trước bộ lọc này.
        tasks = [task for task in tasks if maybe_escalate(task, now) == wanted_priority]
    return [task_brief(task, now) for task in tasks]
