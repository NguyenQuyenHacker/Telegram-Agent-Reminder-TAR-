"""Ghi dòng lịch công việc. Hàm ĐỒNG BỘ — nơi gọi bọc asyncio.to_thread.

Cùng khuôn với `proc/chunks.py`: nhận `session` từ ngoài chứ không tự mở, vì
chúng chạy trong CÙNG transaction với việc ghi source_document và doc_chunk
(xem TAR_agent/graph_admin/helpers/writer.py). Tách transaction là có lúc kho
giữ một tài liệu có chunk mà không có dòng nào — mà nạp lại thì sha256 trùng
nên bị bỏ qua ở nhánh `unchanged`, kẹt cứng.

KHÔNG có hàm đọc ở đây. Đọc bảng này là việc của SQL do LLM sinh ra, chạy qua
`persistence.pool.readonly_tx` — thêm một đường đọc bằng session thường là mở
lại đúng cái lỗ mà RLS được dựng lên để bịt.
"""

import uuid

from sqlmodel import Session, delete

from persistence.models import CongViec


def delete_by_document(session: Session, document_id: uuid.UUID) -> int:
    """Xoá mọi dòng của một tài liệu. KHÔNG commit — nơi gọi giữ transaction.

    CASCADE lo phần xoá khi tài liệu bị xoá HẲN. Hàm này lo phần GHI ĐÈ: tài
    liệu vẫn còn, chỉ nội dung đổi. Thiếu nó thì nạp file tháng 7 sẽ CỘNG THÊM
    vào các dòng tháng 6 chứ không thay chúng.
    """
    result = session.exec(
        delete(CongViec).where(CongViec.document_id == document_id)  # type: ignore[arg-type]
    )
    return result.rowcount or 0


def insert_many(session: Session, rows: list[CongViec]) -> int:
    """Ghi cả lô một lượt. KHÔNG commit — nơi gọi giữ transaction."""
    if not rows:
        return 0
    session.add_all(rows)
    session.flush()
    return len(rows)
