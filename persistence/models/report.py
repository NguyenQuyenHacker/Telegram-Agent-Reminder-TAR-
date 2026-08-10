from datetime import datetime, timezone

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Report(SQLModel, table=True):
    __tablename__ = "report"

    report_id: str = Field(primary_key=True)
    source_hash: str
    group: str | None = Field(default=None, sa_column=Column("group", Text, nullable=True))
    raw_text: str
    received_at: datetime = Field(default_factory=_utcnow)
