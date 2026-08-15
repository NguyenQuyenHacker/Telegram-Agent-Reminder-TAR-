"""Ghi một tài liệu đã embed vào kho. Chỗ DUY NHẤT ghi vào 4 bảng.

    tài liệu chưa có  -> INSERT source_document + INSERT chunk + INSERT dòng
    tài liệu đã có    -> DELETE chunk & dòng cũ + UPDATE source_document
                         + INSERT chunk + INSERT dòng

Cả hai nhánh nằm trong MỘT transaction. Xoá chunk cũ xong mà rơi giữa chừng là
kho giữ một tài liệu "đã nạp" với 0 chunk: tra không ra gì, mà nạp lại thì
sha256 trùng nên bị bỏ qua ở nhánh `unchanged`. Kẹt cứng, phải xoá tay mới gỡ.

`data.cong_viec` ghi Ở ĐÂY, trong đúng transaction đó, chứ không ở một nơi
riêng — đó là lý do bảng ấy nằm cùng database chứ không phải một database thứ
hai: Postgres không có transaction xuyên database.

Hàm ĐỒNG BỘ (Session thường của SQLModel) — nơi gọi bọc asyncio.to_thread.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from langchain_core.documents import Document

from persistence.models import CongViec, DocChunk, SourceDocument
from persistence.pool import get_session
from persistence.proc import chunks as chunks_proc
from persistence.proc import rows as rows_proc
from TAR_agent.graph_admin.helpers.rows import CheckedRow
from TAR_agent.utils.text import chunk_uuid, document_uuid, row_uuid


@dataclass(frozen=True)
class StoredDocument:
    document_id: uuid.UUID
    chunk_count: int
    # Số chunk của bản cũ vừa bị xoá. 0 khi đây là tài liệu mới.
    replaced_chunks: int
    row_count: int = 0
    # Số dòng lịch công việc của bản cũ vừa bị xoá.
    replaced_rows: int = 0


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
    rows: list[CheckedRow] | None = None,
) -> StoredDocument:
    """Ghi (hoặc ghi đè) một tài liệu. `file_name` đã bỏ hậu tố "(n)".

    `documents` là đầu ra thô của loader, `chunks` là kết quả sau splitter.
    Giữ cả hai: `documents` để tái index sau này mà không cần admin gửi lại file.

    `rows` mặc định None chứ không bắt buộc: `extract` được phép trả 0 dòng mà
    lượt nạp vẫn thành công, nên chỗ này không có quyền coi "không có dòng nào"
    là một lỗi gọi hàm.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"Lệch số lượng: {len(chunks)} chunk nhưng {len(embeddings)} vector"
        )

    rows = rows or []
    document_id = document_uuid(project_id, file_name)

    with get_session() as session:
        existing = session.get(SourceDocument, document_id)

        # Bản cũ có thể nhiều chunk hơn bản mới. Xoá sạch rồi ghi lại, không cố
        # cập nhật tại chỗ — chunk_index của hai bản không có lý do gì khớp nhau.
        replaced = (
            chunks_proc.delete_by_document(session, document_id) if existing else 0
        )
        # Dòng lịch công việc cũng vậy, và vì cùng lý do: thứ tự dòng của hai
        # bản không khớp nhau, ghi đè từng dòng là trộn hai lần nạp vào nhau.
        replaced_rows = (
            rows_proc.delete_by_document(session, document_id) if existing else 0
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

        rows_proc.insert_many(
            session,
            [
                CongViec(
                    # uuid5 từ (tài liệu, vị trí) chứ không uuid4, giống
                    # chunk_uuid: nạp lại cùng một file thì dòng thứ 5 vẫn mang
                    # đúng id cũ, soi log hoặc so hai lần nạp mới có nghĩa.
                    row_id=row_uuid(document_id, i),
                    document_id=document_id,
                    project_id=project_id,
                    as_of_date=as_of_date,
                    chunk_id=row.chunk_id,
                    giai_doan=row.giai_doan,
                    nhom=row.nhom,
                    nhom_con=row.nhom_con,
                    cong_viec=row.cong_viec,
                    don_vi=row.don_vi,
                    ngay_bd=row.ngay_bd,
                    ngay_ht=row.ngay_ht,
                    so_ngay=row.so_ngay,
                    can_cu_phap_ly=row.can_cu_phap_ly,
                    ket_qua_dau_ra=row.ket_qua_dau_ra,
                    ghi_chu=row.ghi_chu,
                )
                for i, row in enumerate(rows)
            ],
        )

        session.commit()

    return StoredDocument(
        document_id=document_id,
        chunk_count=len(chunks),
        replaced_chunks=replaced,
        row_count=len(rows),
        replaced_rows=replaced_rows,
    )
