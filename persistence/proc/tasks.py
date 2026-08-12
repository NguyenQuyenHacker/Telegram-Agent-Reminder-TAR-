from datetime import date, datetime, timedelta, timezone

from sqlmodel import col, or_, select

from app.core.config import settings
from app.core.task_code import parse_code
from persistence.models.task import Priority, Task, TaskStatus
from persistence.pool import get_session
from persistence.proc.group_codes import allocate_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _undo_deadline() -> datetime:
    """R8: mốc sớm nhất còn được hoàn tác."""
    return _utcnow() - timedelta(hours=settings.undo_window_hours)


def get_due_tasks(now: datetime) -> list[Task]:
    """Chỉ việc đang chờ: xong hoặc đã hủy thì không nhắc nữa."""
    with get_session() as s:
        stmt = (
            select(Task)
            .where(Task.status == TaskStatus.pending, Task.next_remind_at <= now)
            .order_by(Task.next_remind_at)
        )
        return list(s.exec(stmt).all())


def get_task(task_id: str) -> Task | None:
    with get_session() as s:
        return s.get(Task, task_id)


def upsert_task(
    task_id: str,
    group: str,
    content: str,
    due_date: date | None,
    priority: Priority,
    next_remind_at: datetime,
) -> Task:
    """R7: đồng bộ lại không tạo trùng; việc đã xong hoặc đã hủy thì giữ nguyên."""
    with get_session() as s:
        existing = s.get(Task, task_id)
        if existing is not None:
            if existing.status in (TaskStatus.done, TaskStatus.cancelled):
                return existing
            existing.group = group
            existing.content = content
            existing.due_date = due_date
            existing.priority = priority
            existing.updated_at = _utcnow()
            s.add(existing)
            s.commit()
            s.refresh(existing)
            return existing

        # Mã chỉ cấp lúc tạo mới. Dòng cũ giữ nguyên mã kể cả khi tên nhóm đổi:
        # mã đã in ra cho người dùng rồi, đổi là họ gõ vào chỗ không còn ai.
        task = Task(
            task_id=task_id,
            code=allocate_code(group),
            group=group,
            content=content,
            due_date=due_date,
            priority=priority,
            next_remind_at=next_remind_at,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def mark_done(task_id: str) -> Task | None:
    """R8: đánh dấu xong, ghi mốc thời gian để phục vụ hoàn tác."""
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None:
            return None
        now = _utcnow()
        task.status = TaskStatus.done
        task.done_at = now
        task.updated_at = now
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def undo_done(task_id: str) -> Task | None:
    """R8: chỉ cho hoàn tác nếu đánh dấu xong chưa quá cửa sổ cho phép."""
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None or task.status != TaskStatus.done or task.done_at is None:
            return None
        if task.done_at < _undo_deadline():
            return None
        now = _utcnow()
        task.status = TaskStatus.pending
        task.done_at = None
        task.next_remind_at = now
        task.updated_at = now
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def cancel_task(task_id: str) -> Task | None:
    """Bỏ việc. Không xóa dòng: còn khôi phục được, và gửi lại báo cáo cũ không
    hồi sinh việc đã bỏ (upsert_task thấy cancelled thì để yên)."""
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None:
            return None
        now = _utcnow()
        task.status = TaskStatus.cancelled
        task.cancelled_at = now
        task.updated_at = now
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def undo_cancel(task_id: str) -> Task | None:
    """R8: đối xứng undo_done, cùng cửa sổ thời gian."""
    with get_session() as s:
        task = s.get(Task, task_id)
        if (
            task is None
            or task.status != TaskStatus.cancelled
            or task.cancelled_at is None
        ):
            return None
        if task.cancelled_at < _undo_deadline():
            return None
        now = _utcnow()
        task.status = TaskStatus.pending
        task.cancelled_at = None
        task.next_remind_at = now
        task.updated_at = now
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def set_due_date(
    task_id: str, new_due: date | None, next_remind_at: datetime
) -> Task | None:
    """Đổi hạn và dời luôn mốc nhắc, để lịch nhắc chạy theo hạn mới.

    next_remind_at do tầng gọi tính sẵn (giống reschedule_task): tầng dữ liệu
    không biết khung giờ nhắc, đó là quy tắc nghiệp vụ ở app/core/priority.py.
    """
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None:
            return None
        task.due_date = new_due
        task.next_remind_at = next_remind_at
        task.updated_at = _utcnow()
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def reschedule_task(
    task_id: str,
    next_remind_at: datetime,
    last_reminded_at: datetime,
    priority: Priority | None = None,
) -> Task | None:
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None:
            return None
        task.next_remind_at = next_remind_at
        task.last_reminded_at = last_reminded_at
        if priority is not None:
            task.priority = priority
        task.updated_at = _utcnow()
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def find_tasks_by_reference(ref: str, limit: int = 5) -> list[Task]:
    """Tìm việc theo cách người dùng nhắc tới nó: mã việc, hoặc một phần nội dung.

    Chỉ tìm trong việc đang chờ — không ai "báo xong" một việc đã xong.
    Gõ đúng mã thì ra tối đa một dòng, khỏi phải hỏi lại.
    """
    pending = select(Task).where(Task.status == TaskStatus.pending)
    code = parse_code(ref)
    with get_session() as s:
        if code is not None:
            matches = list(s.exec(pending.where(Task.code == code).limit(limit)).all())
            # Không match thì đây không phải mã việc mà chỉ TRÔNG như mã
            # ("abc123"), tìm tiếp theo nội dung thay vì báo không thấy.
            if matches:
                return matches

        keyword = f"%{ref.strip()}%"
        stmt = pending.where(
            or_(col(Task.content).ilike(keyword), col(Task.group).ilike(keyword))
        )
        return list(s.exec(stmt.order_by(Task.due_date).limit(limit)).all())


def query_tasks(
    group: str | None = None,
    status: TaskStatus | None = None,
    priority: Priority | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    limit: int = 50,
) -> list[Task]:
    with get_session() as s:
        stmt = select(Task)
        if group:
            stmt = stmt.where(Task.group == group)
        if status:
            stmt = stmt.where(Task.status == status)
        if priority:
            stmt = stmt.where(Task.priority == priority)
        if due_from:
            stmt = stmt.where(Task.due_date >= due_from)
        if due_to:
            stmt = stmt.where(Task.due_date <= due_to)
        stmt = stmt.order_by(Task.due_date).limit(limit)
        return list(s.exec(stmt).all())
