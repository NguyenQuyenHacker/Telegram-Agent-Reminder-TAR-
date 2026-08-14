"""Ghi một tài liệu đã embed vào kho. Chỗ DUY NHẤT ghi vào 3 bảng.

    tài liệu chưa có  -> INSERT source_document + INSERT chunk
    tài liệu đã có    -> DELETE chunk cũ + UPDATE source_document + INSERT chunk

Cả hai nhánh nằm trong MỘT transaction. Xoá chunk cũ xong mà rơi giữa chừng là
kho giữ một tài liệu "đã nạp" với 0 chunk: tra không ra gì, mà nạp lại thì
sha256 trùng nên bị bỏ qua ở nhánh `unchanged`. Kẹt cứng, phải xoá tay mới gỡ.

Hàm ĐỒNG BỘ (Session thường của SQLModel) — nơi gọi bọc asyncio.to_thread.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from langchain_core.documents import Document

from persistence.models import DocChunk, SourceDocument
from persistence.pool import get_session
from persistence.proc import chunks as chunks_proc
from TAR_agent.utils.text import chunk_uuid, document_uuid


@dataclass(frozen=True)
class StoredDocument:
    document_id: uuid.UUID
    chunk_count: int
    # Số chunk của bản cũ vừa bị xoá. 0 khi đây là tài liệu mới.
    replaced_chunks: int


def save(
    *,
    project_id: uuid.UUID,
    file_name: str,
    file_kind: str,
    content_sha256: str,
    as_of_date: date,
    documents: list[Document],
    chunks: list[Document],
    embeddings: list[list[float]],
    uploaded_by: int,
) -> StoredDocument:
    """Ghi (hoặc ghi đè) một tài liệu. `file_name` đã bỏ hậu tố "(n)".

    `documents` là đầu ra thô của loader, `chunks` là kết quả sau splitter.
    Giữ cả hai: `documents` để tái index sau này mà không cần admin gửi lại file.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"Lệch số lượng: {len(chunks)} chunk nhưng {len(embeddings)} vector"
        )

    document_id = document_uuid(project_id, file_name)

    with get_session() as session:
        existing = session.get(SourceDocument, document_id)

        # Bản cũ có thể nhiều chunk hơn bản mới. Xoá sạch rồi ghi lại, không cố
        # cập nhật tại chỗ — chunk_index của hai bản không có lý do gì khớp nhau.
        replaced = (
            chunks_proc.delete_by_document(session, document_id) if existing else 0
        )

        if existing is None:
            existing = SourceDocument(
                document_id=document_id,
                project_id=project_id,
                file_name=file_name,
                file_kind=file_kind,  # type: ignore[arg-type]
            )
            session.add(existing)

        existing.content_sha256 = content_sha256
        existing.as_of_date = as_of_date
        existing.documents = [
            {"page_content": d.page_content, "metadata": d.metadata} for d in documents
        ]
        existing.chunk_count = len(chunks)
        existing.uploaded_by = uploaded_by
        existing.uploaded_at = datetime.now(timezone.utc)

        # flush trước khi ghi chunk: doc_chunk.document_id là khoá ngoại, dòng
        # cha phải hiện diện trong transaction trước.
        session.flush()

        chunks_proc.insert_many(
            session,
            [
                DocChunk(
                    chunk_id=chunk_uuid(document_id, i),
                    document_id=document_id,
                    project_id=project_id,
                    as_of_date=as_of_date,
                    chunk_index=i,
                    content=chunk.page_content,
                    chunk_metadata=chunk.metadata,
                    embedding=vector,
                )
                for i, (chunk, vector) in enumerate(zip(chunks, embeddings))
            ],
        )

        session.commit()

    return StoredDocument(
        document_id=document_id,
        chunk_count=len(chunks),
        replaced_chunks=replaced,
    )
