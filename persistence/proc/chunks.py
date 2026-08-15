"""Ghi và tìm chunk vector. Hàm ĐỒNG BỘ — nơi gọi bọc asyncio.to_thread.

Hai hàm ghi ở đây nhận `session` từ ngoài chứ không tự mở: chúng chạy trong
cùng transaction với việc ghi source_document (xem TAR_agent/graph_admin/helpers/writer.py).
Tách transaction là có lúc kho giữ một tài liệu "đã nạp" với 0 chunk.
"""

import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import Session, delete, select

from persistence.models import DocChunk, SourceDocument
from persistence.pool import get_session


def delete_by_document(session: Session, document_id: uuid.UUID) -> int:
    """Xoá mọi chunk của một tài liệu. KHÔNG commit — nơi gọi giữ transaction."""
    result = session.exec(
        delete(DocChunk).where(DocChunk.document_id == document_id)  # type: ignore[arg-type]
    )
    return result.rowcount or 0


def insert_many(session: Session, chunks: list[DocChunk]) -> int:
    """Ghi cả lô một lượt. KHÔNG commit — nơi gọi giữ transaction.

    `add_all` + một `flush`, không commit từng dòng: một file có thể ra vài trăm
    chunk, commit lẻ là vài trăm lượt đi về với Neon.
    """
    if not chunks:
        return 0
    session.add_all(chunks)
    session.flush()
    return len(chunks)


def corpus_fingerprint(project_id: uuid.UUID) -> tuple[int, datetime | None]:
    """Dấu vân tay của kho một dự án: (số chunk, lần nạp gần nhất).

    Dùng để vô hiệu chỉ mục BM25 đang giữ trong RAM. Hai con số này đổi khi và
    chỉ khi có tài liệu được nạp, ghi đè hoặc xoá — đủ để phát hiện, và rẻ hơn
    nhiều so với đọc lại cả kho để so.

    `max(uploaded_at)` chứ không chỉ `count`: ghi đè một tài liệu bằng bản mới
    có ĐÚNG số chunk như cũ là chuyện thường (sửa vài ô trong file xlsx), lúc
    đó riêng `count` không đổi và chỉ mục cũ sống mãi với nội dung đã lỗi thời.
    """
    with get_session() as session:
        count = session.exec(
            select(func.count(DocChunk.chunk_id)).where(  # type: ignore[arg-type]
                DocChunk.project_id == project_id
            )
        ).one()
        latest = session.exec(
            select(func.max(SourceDocument.uploaded_at)).where(  # type: ignore[arg-type]
                SourceDocument.project_id == project_id
            )
        ).one()
        return count, latest


def load_corpus(project_id: uuid.UUID) -> list[tuple[DocChunk, SourceDocument]]:
    """TOÀN BỘ chunk của một dự án — nguyên liệu dựng chỉ mục BM25.

    `load_only` chứ không `select(DocChunk)`: hàm này đọc CẢ BẢNG của dự án chứ
    không phải 5 dòng, mà cột `embedding` là 768 float mỗi dòng. Kéo nó về rồi
    vứt đi là vài chục MB đi qua mạng cho mỗi lần dựng chỉ mục.

    Hệ quả cố ý: `chunk.embedding` của object trả về KHÔNG đọc được (session đã
    đóng, thuộc tính hoãn tải sẽ ném). Ai cần vector thì gọi `search`.
    """
    with get_session() as session:
        rows = session.exec(
            select(DocChunk, SourceDocument)
            .join(SourceDocument, SourceDocument.document_id == DocChunk.document_id)  # type: ignore[arg-type]
            .where(DocChunk.project_id == project_id)
            .options(
                load_only(
                    DocChunk.content,  # type: ignore[arg-type]
                    DocChunk.as_of_date,  # type: ignore[arg-type]
                    DocChunk.chunk_metadata,  # type: ignore[arg-type]
                ),
                load_only(SourceDocument.file_name),  # type: ignore[arg-type]
            )
            .order_by(DocChunk.document_id, DocChunk.chunk_index)  # type: ignore[arg-type]
        ).all()
        return [(chunk, document) for chunk, document in rows]


def search(
    project_id: uuid.UUID, embedding: list[float], k: int = 5
) -> list[tuple[DocChunk, SourceDocument, float]]:
    """k chunk gần nghĩa nhất TRONG một dự án, kèm tài liệu nguồn và khoảng cách.

    `project_id` là tham số đầu, không mặc định, không nhận None. Bỏ bộ lọc này
    là trả lời câu hỏi của dự án A bằng tài liệu dự án B — và câu trả lời đó
    trông vẫn rất thật.

    `<=>` là cosine distance, khớp index hnsw `vector_cosine_ops` trong
    schema.sql. Đổi sang `<->` hay `<#>` thì Postgres BỎ index và quét toàn
    bảng: vẫn ra kết quả, chỉ chậm dần theo số dòng cho tới lúc không dùng được.
    """
    distance = DocChunk.embedding.cosine_distance(embedding)  # type: ignore[attr-defined]
    with get_session() as session:
        rows = session.exec(
            select(DocChunk, SourceDocument, distance.label("distance"))
            .join(SourceDocument, SourceDocument.document_id == DocChunk.document_id)  # type: ignore[arg-type]
            .where(DocChunk.project_id == project_id)
            .order_by(distance)
            .limit(k)
        ).all()
        return [(chunk, document, dist) for chunk, document, dist in rows]
