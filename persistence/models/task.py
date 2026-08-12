import uuid
from datetime import date, datetime, timezone
from enum import Enum

from sqlmodel import Field, Relationship, SQLModel

from persistence.models.project_group import ProjectGroup


class TaskStatus(str, Enum):
    pending = "pending"
    done = "done"
    # Hủy chứ không xóa dòng: hủy nhầm còn khôi phục được, và gửi lại báo cáo cũ
    # không hồi sinh việc đã bỏ (upsert_task thấy cancelled thì giữ nguyên).
    cancelled = "cancelled"


class Priority(str, Enum):
    urgent = "urgent"
    normal = "normal"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Task(SQLModel, table=True):
    __tablename__ = "task"

    # task_id là băm (nhóm + nội dung) — căn cước idempotent của R7, người dùng
    # không đọc nổi. code là mã người đọc được ("TB-002"), cấp lúc tạo mới.
    task_id: str = Field(primary_key=True)
    code: str | None = Field(default=None, unique=True)
    group_id: uuid.UUID = Field(foreign_key="project_group.group_id", index=True)
    # Nạp sẵn cùng câu SELECT chính: đối tượng trả ra khỏi get_session() đã rời
    # session, lazy load lúc đó là DetachedInstanceError chứ không phải query.
    project_group: ProjectGroup | None = Relationship(
        sa_relationship_kwargs={"lazy": "joined"}
    )
    # Đã gọt "(hạn ...)" và tiền tố "ƯU TIÊN:" — xem app/core/task_text.py.
    content: str
    due_date: date | None = None
    priority: Priority
    status: TaskStatus = TaskStatus.pending
    next_remind_at: datetime
    last_reminded_at: datetime | None = None
    done_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def group_name(self) -> str:
        """Tên nhóm để hiển thị. Rỗng nếu dòng nhóm đã biến mất khỏi DB."""
        return self.project_group.name if self.project_group else ""
