import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from persistence.models.base import utcnow


class Project(SQLModel, table=True):
    """Một dự án — đơn vị gom tài liệu, và là bộ lọc bắt buộc của mọi truy vấn.

    project_id = uuid5(namespace, normalized_name), xem TAR_agent/utils/text.py:name_uuid.
    Suy được từ tên nên không cần tra DB, và mọi biến thể cách viết ("App Trưởng
    thôn" / "app trưởng thôn" / "2/ App Trưởng thôn") rơi về đúng một dòng.
    """

    __tablename__ = "project"

    project_id: uuid.UUID = Field(primary_key=True)
    name: str  # tên của lần gặp gần nhất, chỉ để hiển thị
    normalized_name: str = Field(unique=True)  # thứ sinh ra project_id, giữ để tra ngược
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
