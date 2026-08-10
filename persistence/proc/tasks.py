from datetime import date, datetime, timedelta, timezone

from sqlmodel import select

from persistence.models.task import Priority, Task, TaskStatus
from persistence.pool import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_due_tasks(now: datetime) -> list[Task]:
    with get_session() as s:
        stmt = (
            select(Task)
            .where(Task.status != TaskStatus.done, Task.next_remind_at <= now)
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
    remind_interval_min: int,
    next_remind_at: datetime,
) -> Task:
    """R7: đồng bộ lại không tạo trùng; task đã done thì giữ nguyên done."""
    with get_session() as s:
        existing = s.get(Task, task_id)
        if existing is not None:
            if existing.status == TaskStatus.done:
                return existing
            existing.group = group
            existing.content = content
            existing.due_date = due_date
            existing.priority = priority
            existing.remind_interval_min = remind_interval_min
            existing.updated_at = _utcnow()
            s.add(existing)
            s.commit()
            s.refresh(existing)
            return existing

        task = Task(
            task_id=task_id,
            group=group,
            content=content,
            due_date=due_date,
            priority=priority,
            remind_interval_min=remind_interval_min,
            next_remind_at=next_remind_at,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


def mark_done(task_id: str) -> Task | None:
    """R8: đánh dấu xong, ghi mốc thời gian để phục vụ hoàn tác trong 24h."""
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
    """R8: chỉ cho hoàn tác nếu đánh dấu xong chưa quá 24 giờ."""
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None or task.status != TaskStatus.done or task.done_at is None:
            return None
        if task.done_at < _utcnow() - timedelta(hours=24):
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


def snooze_task(task_id: str, next_remind_at: datetime, new_interval_min: int) -> Task | None:
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None:
            return None
        task.status = TaskStatus.snoozed
        task.next_remind_at = next_remind_at
        task.remind_interval_min = new_interval_min
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
    remind_interval_min: int | None = None,
) -> Task | None:
    with get_session() as s:
        task = s.get(Task, task_id)
        if task is None:
            return None
        task.next_remind_at = next_remind_at
        task.last_reminded_at = last_reminded_at
        if priority is not None:
            task.priority = priority
        if remind_interval_min is not None:
            task.remind_interval_min = remind_interval_min
        task.updated_at = _utcnow()
        s.add(task)
        s.commit()
        s.refresh(task)
        return task


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
