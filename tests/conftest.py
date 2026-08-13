from datetime import date, datetime

import pytest

from app.core.datetime_utils import TZ
from app.core.task_code import derive_prefix, group_uuid, normalize_group
from persistence.models.project_group import ProjectGroup
from persistence.models.task import Priority, Task, TaskStatus


@pytest.fixture
def now():
    """Mốc cố định trong khung giờ nhắc, để test không phụ thuộc lúc chạy."""
    return datetime(2026, 7, 17, 9, 0, tzinfo=TZ)


@pytest.fixture
def make_task(now):
    def _make(
        code: str,
        content: str = "Việc nào đó",
        due_date: date | None = None,
        priority: Priority = Priority.normal,
        group: str = "Nhóm A",
        parent_task_id: str | None = None,
        status: TaskStatus = TaskStatus.pending,
    ) -> Task:
        # Gắn sẵn dòng nhóm chứ không để None: task.group_name đọc qua nó, và
        # test dựng Task trong bộ nhớ nên không có session nào nạp hộ.
        group_id = group_uuid(group)
        return Task(
            task_id=code.lower(),
            code=code,
            group_id=group_id,
            project_group=ProjectGroup(
                group_id=group_id,
                name=group,
                normalized_name=normalize_group(group),
                prefix=derive_prefix(group),
            ),
            parent_task_id=parent_task_id,
            content=content,
            due_date=due_date,
            priority=priority,
            status=status,
            next_remind_at=now,
        )

    return _make
