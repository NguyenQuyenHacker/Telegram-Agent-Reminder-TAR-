"""Truy vấn bảng source_document. Hàm ĐỒNG BỘ — nơi gọi bọc asyncio.to_thread.

Danh tính của một tài liệu là (project_id, file_name). `find_by_name` là cửa
vào của cả cơ chế ghi đè: có dòng rồi thì đây là bản sửa, không thì là bản mới.
"""

import uuid

from sqlalchemy import func
from sqlmodel import select

from persistence.models import DocChunk, Project, SourceDocument
from persistence.pool import get_session


def find_by_name(project_id: uuid.UUID, file_name: str) -> SourceDocument | None:
    """Tài liệu cùng tên trong dự án này, nếu có.

    Gọi TRƯỚC khi parse và embed: so `content_sha256` của bản đang cầm với dòng
    trả về là biết ngay nội dung có đổi không, mà chưa tốn một đồng embedding.
    """
    with get_session() as session:
        return session.exec(
            select(SourceDocument).where(
                SourceDocument.project_id == project_id,
                SourceDocument.file_name == file_name,
            )
        ).first()


def get_document(document_id: uuid.UUID) -> SourceDocument | None:
    with get_session() as session:
        return session.get(SourceDocument, document_id)


def list_documents(project_id: uuid.UUID | None = None) -> list[SourceDocument]:
    """Tài liệu của một dự án (hoặc cả kho), mốc dữ liệu mới nhất trước."""
    with get_session() as session:
        stmt = select(SourceDocument)
        if project_id is not None:
            stmt = stmt.where(SourceDocument.project_id == project_id)
        return list(
            session.exec(
                stmt.order_by(
                    SourceDocument.as_of_date.desc(),  # type: ignore[attr-defined]
                    SourceDocument.file_name,
                )
            ).all()
        )


def reset_hashes(project_id: uuid.UUID) -> int:
    """Xoá `content_sha256` của mọi tài liệu trong một dự án. Trả số dòng đã sửa.

    Đây là thứ gỡ nút cho lệnh `/reindex`. `check_file` chặn ở nhánh
    `unchanged` khi hash TRÙNG, và nó chặn TRƯỚC `parse` — nên tài liệu nạp từ
    thời chưa có node `extract` không có đường nào chạy qua nó: nạp lại đúng
    file đó thì hash vẫn trùng và lượt nạp bị bỏ qua.

    Xoá hash chứ không xoá tài liệu: chunk và vector cũ vẫn phục vụ được trong
    lúc chờ admin gửi lại file. Đặt chuỗi rỗng vì cột `NOT NULL` — và chuỗi
    rỗng không bao giờ bằng một chuỗi sha256 thật, nên lần nạp sau chắc chắn đi
    tiếp.

    Không tự tái tạo dòng từ cột `documents`: nó chỉ giữ ĐẦU RA loader (text),
    không giữ lưới ô, nên `.xlsx` không extract lại được từ DB. Phải có file gốc.
    """
    with get_session() as session:
        documents = session.exec(
            select(SourceDocument).where(SourceDocument.project_id == project_id)
        ).all()
        for document in documents:
            document.content_sha256 = ""
        session.commit()
        return len(documents)


def delete_document(document_id: uuid.UUID) -> bool:
    """Xoá tài liệu. Chunk đi theo nhờ ON DELETE CASCADE."""
    with get_session() as session:
        document = session.get(SourceDocument, document_id)
        if document is None:
            return False
        session.delete(document)
        session.commit()
        return True


def stats() -> tuple[int, int, int]:
    """(số dự án, số tài liệu, số chunk) — cho lệnh thống kê."""
    with get_session() as session:
        return (
            session.exec(select(func.count(Project.project_id))).one(),
            session.exec(select(func.count(SourceDocument.document_id))).one(),
            session.exec(select(func.count(DocChunk.chunk_id))).one(),
        )
