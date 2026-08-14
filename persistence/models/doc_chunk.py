import uuid
from datetime import date
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Số chiều vector. gemini-embedding-001 ra 3072 chiều mặc định nhưng cắt xuống
# 768 được (Matryoshka) — TAR_agent/utils/embed.py truyền output_dimensionality bằng đúng
# hằng số này. Đổi số này là phải đổi cả cột VECTOR trong schema.sql VÀ re-embed
# toàn bộ chunk đang có (đọc lại từ cột `documents` của source_document).
EMBEDDING_DIM = 768


class DocChunk(SQLModel, table=True):
    """Một đoạn văn đã cắt, kèm vector của nó.

    Ánh xạ 1-1 với `Document` của LangChain: `content` là `page_content`,
    `chunk_metadata` là `metadata`.

    `project_id` và `as_of_date` lặp lại từ source_document dù suy được qua
    document_id: retrieval BẮT BUỘC lọc theo dự án, để cột ngay đây thì bộ lọc
    là một WHERE thẳng chứ không phải một JOIN mà ai đó có thể quên viết — và
    mỗi đoạn tự mang mốc dữ liệu của nó khi tìm kiếm trả về cả bản tháng 6 lẫn
    tháng 7. Không lệch được: ghi đè tài liệu là xoá và ghi lại TOÀN BỘ chunk,
    không UPDATE lẻ tẻ.
    """

    __tablename__ = "doc_chunk"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index"),)

    chunk_id: uuid.UUID = Field(primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="source_document.document_id", index=True
    )
    project_id: uuid.UUID = Field(index=True)
    as_of_date: date

    chunk_index: int
    content: str

    # Tên thuộc tính KHÔNG được là `metadata` — tên dành riêng của SQLAlchemy
    # declarative, đặt trùng là nổ ngay lúc định nghĩa lớp. Cột dưới DB vẫn "metadata".
    chunk_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False, server_default="{}"),
    )

    embedding: list[float] = Field(sa_column=Column(Vector(EMBEDDING_DIM), nullable=False))
