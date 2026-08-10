"""Dọn bảng checkpoint của LangGraph.

AsyncPostgresSaver ghi MỘT checkpoint cho mỗi bước của mỗi lượt chạy graph và
không bao giờ tự xoá. Bot chỉ phục vụ một chat_id nên tất cả dồn vào một thread:
cắt lịch sử trong state (xem ReminderAgent._recent_history) chỉ làm mỗi
checkpoint nhẹ đi, chứ không giảm SỐ checkpoint.

Chỉ xoá bản ghi cũ hơn N ngày, và luôn giữ lại checkpoint mới nhất của mỗi
thread — xoá nhầm cái đó là mất luôn hội thoại đang treo ở interrupt.

Bảng do LangGraph tự tạo (.setup()), không có model SQLModel nên phải viết SQL
thô. Tên bảng theo langgraph-checkpoint-postgres 2.x.
"""

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text

from app.core.datetime_utils import now_local
from persistence.pool import get_session

log = logging.getLogger(__name__)

# checkpoint_blobs/_writes tham chiếu checkpoint qua (thread_id, checkpoint_ns,
# checkpoint_id) nhưng KHÔNG có khoá ngoại, nên phải tự xoá con trước cha.
_DELETE_SQL = """
WITH keep AS (
    SELECT DISTINCT ON (thread_id, checkpoint_ns) thread_id, checkpoint_ns, checkpoint_id
    FROM checkpoints
    ORDER BY thread_id, checkpoint_ns, checkpoint_id DESC
),
doomed AS (
    SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id
    FROM checkpoints c
    LEFT JOIN keep k USING (thread_id, checkpoint_ns, checkpoint_id)
    WHERE k.checkpoint_id IS NULL
      AND c.checkpoint_id < :cutoff_uuid
),
del_writes AS (
    DELETE FROM checkpoint_writes w
    USING doomed d
    WHERE w.thread_id = d.thread_id
      AND w.checkpoint_ns = d.checkpoint_ns
      AND w.checkpoint_id = d.checkpoint_id
    RETURNING 1
)
DELETE FROM checkpoints c
USING doomed d
WHERE c.thread_id = d.thread_id
  AND c.checkpoint_ns = d.checkpoint_ns
  AND c.checkpoint_id = d.checkpoint_id
"""


def _cutoff_uuid(days: int) -> str:
    """Mốc UUIDv6 tương ứng 'N ngày trước'.

    checkpoint_id là UUIDv6 — 60 bit đầu là timestamp, nên so sánh chuỗi UUID
    cũng chính là so sánh thời gian. Nhờ vậy lọc theo tuổi được mà không cần
    join sang cột thời gian nào (bảng không có sẵn cột đó).
    """
    cutoff = now_local() - timedelta(days=days)
    # 100-nanosecond intervals kể từ 1582-10-15, theo chuẩn UUID v1/v6
    ticks = int((cutoff.timestamp() + 12219292800) * 10_000_000)
    hi, mid, lo = (ticks >> 28) & 0xFFFFFFFF, (ticks >> 12) & 0xFFFF, ticks & 0x0FFF
    return str(UUID(fields=(hi, mid, 0x6000 | lo, 0x80, 0x00, 0x000000000000)))


def purge_old_checkpoints(days: int) -> int:
    """Xoá checkpoint cũ hơn `days` ngày. Trả về số dòng đã xoá ở bảng chính."""
    # execute() chứ không phải exec(): exec() của SQLModel dành cho select()
    # có kiểu, còn đây là SQL thô và ta cần rowcount.
    with get_session() as s:
        result = s.execute(text(_DELETE_SQL), {"cutoff_uuid": _cutoff_uuid(days)})
        s.commit()
        deleted = result.rowcount or 0
    log.info("PURGE: xoá %d checkpoint cũ hơn %d ngày", deleted, days)
    return deleted
