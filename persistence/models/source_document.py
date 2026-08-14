import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from persistence.models.base import utcnow


class FileKind(str, Enum):
    txt = "txt"
    xlsx = "xlsx"


class SourceDocument(SQLModel, table=True):
    """Một tài liệu trong kho.

    DANH TÍNH là (project_id, file_name). Nạp lại cùng tên là GHI ĐÈ: xoá sạch
    chunk cũ, ghi chunk mới, tên giữ nguyên. Nhờ vậy TiendoT6.xlsx và
    TiendoT7.xlsx là hai tài liệu cùng sống, còn TiendoT6(1).xlsx là bản sửa
    của TiendoT6.xlsx.
    """

    __tablename__ = "source_document"
    __table_args__ = (UniqueConstraint("project_id", "file_name"),)

    # uuid5 theo TÊN (TAR_agent/utils/text.py:document_uuid) -> ghi đè giữ nguyên id
    document_id: uuid.UUID = Field(primary_key=True)
    project_id: uuid.UUID = Field(foreign_key="project.project_id", index=True)

    file_name: str  # đã bỏ hậu tố "(n)" Telegram/Windows thêm khi tải trùng tên
    file_kind: FileKind

    content_sha256: str  # băm BYTES THÔ, KHÔNG unique — chỉ để hỏi "nội dung có đổi không"
    as_of_date: date  # mốc DỮ LIỆU của file, không phải ngày nạp

    # Đầu ra thô của loader: [{"page_content": ..., "metadata": {...}}, ...].
    # Giữ lại để tái index (đổi chunk_size, đổi model embedding) mà khỏi bắt
    # admin gửi lại file.
    documents: list[dict[str, Any]] = Field(sa_column=Column(JSONB, nullable=False))
    chunk_count: int = 0

    # BigInteger: Telegram user id đã vượt 2^31, để INTEGER là tràn số
    uploaded_by: int = Field(sa_column=Column(BigInteger, nullable=False))
    uploaded_at: datetime = Field(default_factory=utcnow)
