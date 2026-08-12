from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


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
    # "group" là từ khoá SQL nên phải khai báo tên cột tường minh
    group: str = Field(sa_column=Column("group", Text, nullable=False))
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
