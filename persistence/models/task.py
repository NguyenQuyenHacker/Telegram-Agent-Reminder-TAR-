from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


class TaskStatus(str, Enum):
    pending = "pending"
    done = "done"
    snoozed = "snoozed"


class Priority(str, Enum):
    urgent = "urgent"
    normal = "normal"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Task(SQLModel, table=True):
    __tablename__ = "task"

    task_id: str = Field(primary_key=True)
    # "group" là từ khoá SQL nên phải khai báo tên cột tường minh
    group: str = Field(sa_column=Column("group", Text, nullable=False))
    content: str
    due_date: date | None = None
    priority: Priority
    status: TaskStatus = TaskStatus.pending
    remind_interval_min: int
    next_remind_at: datetime
    last_reminded_at: datetime | None = None
    done_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
