"""Cửa ra DUY NHẤT của nhánh nạp: dựng event và dọn file tạm.

Mọi đường — lỗi, huỷ, trùng, thành công — đều đổ về đây, nên không lượt nào kết
thúc im lặng và không đường nào bỏ sót việc xoá file tạm.

Chỉ trả `kind` + dữ liệu thô; câu chữ là việc của app/telegram/render.py.

Số dòng lịch công việc in ra ở MỌI lượt ghi, KỂ CẢ khi bằng 0. `extract` được
phép trả 0 dòng mà lượt nạp vẫn tính là thành công — im lặng ở đúng chỗ đó thì
admin tưởng đã nạp đủ, và chỉ phát hiện lúc bot trả lời sai một câu đếm.
"""

import logging

from TAR_agent.graph_admin.state import AdminState

log = logging.getLogger(__name__)

_BY_OUTCOME = {
    "created": "ingest_done",
    "updated": "ingest_updated",
    "unchanged": "ingest_unchanged",
    "cancelled": "ingest_cancelled",
}


def _cleanup(state: AdminState) -> None:
    """Xoá file tạm. Hỏng thì log chứ không làm chết lượt trả lời."""
    upload = state.get("upload")
    if upload is None:
        return
    try:
        upload.path.unlink(missing_ok=True)
    except OSError:
        log.warning("Không xoá được file tạm %s", upload.path, exc_info=True)


def report(state: AdminState) -> dict:
    try:
        if error := state.get("error"):
            event = {
                "kind": "ingest_failed",
                "data": {
                    "reason": error,
                    "file_name": state.get("base_name")
                    or (state["upload"].file_name if state.get("upload") else None),
                },
            }
        else:
            outcome = state.get("outcome") or "created"
            # unchanged/cancelled không chạy qua parse nên chưa có chunk mới.
            # Con số đáng nói lúc đó là số chunk của bản ĐANG có trong kho.
            wrote = outcome in ("created", "updated")
            event = {
                "kind": _BY_OUTCOME[outcome],
                "data": {
                    "file_name": state.get("base_name"),
                    "project_name": state.get("project_name"),
                    "chunk_count": (
                        state.get("chunk_count", 0)
                        if wrote
                        else state.get("existing_chunk_count", 0)
                    ),
                    "replaced_chunk_count": (
                        state.get("existing_chunk_count", 0) if wrote else 0
                    ),
                    "as_of_date": (
                        state["as_of_date"].isoformat()
                        if state.get("as_of_date")
                        else None
                    ),
                    # Năm khoá dưới chỉ có nghĩa ở nhánh ĐÃ GHI. unchanged /
                    # cancelled không chạy qua extract nên chúng rỗng, và nói
                    # "ghi 0 dòng" ở một lượt không ghi gì là nói dối.
                    "row_count": state.get("row_count", 0) if wrote else 0,
                    "rejected_count": (
                        state.get("rows_rejected_count", 0) if wrote else 0
                    ),
                    # Cắt bớt ở tầng render, không ở đây: lõi trả sự thật đầy
                    # đủ, giới hạn 4096 ký tự là chuyện của Telegram.
                    "rejected_reasons": (
                        (state.get("rows_rejected") or []) if wrote else []
                    ),
                    "extract_error": state.get("extract_error") if wrote else None,
                    "column_map": (state.get("column_map") or {}) if wrote else {},
                },
            }
        return {"outbox": [event]}
    finally:
        _cleanup(state)
