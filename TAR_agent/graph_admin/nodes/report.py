"""Cửa ra DUY NHẤT của nhánh nạp: dựng event và dọn file tạm.

Mọi đường — lỗi, huỷ, trùng, thành công — đều đổ về đây, nên không lượt nào kết
thúc im lặng và không đường nào bỏ sót việc xoá file tạm.

Chỉ trả `kind` + dữ liệu thô; câu chữ là việc của app/telegram/render.py.
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
                },
            }
        return {"outbox": [event]}
    finally:
        _cleanup(state)
