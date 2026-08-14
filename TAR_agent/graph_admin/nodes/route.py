"""Có `upload` thì đi nhánh nạp, không thì nhánh chữ. Không gọi LLM.

Đồng thời là chỗ DUY NHẤT dọn state của lượt trước: `route` luôn chạy đầu mỗi
LƯỢT MỚI (resume sau interrupt thì không, nó chạy tiếp từ node đang dừng).

Các khoá ngoài `messages` không có reducer nên LangGraph giữ nguyên giá trị cũ
nếu node không ghi đè. Không dọn thì một chat từng dính `error="no_projects"`
sẽ bị rẽ thẳng tới `report` ở mọi lượt sau, báo lại đúng lỗi cũ đã hết đúng.
"""

import logging

from TAR_agent.graph_admin.state import AdminState

log = logging.getLogger(__name__)


def route(state: AdminState) -> dict:
    upload = state.get("upload")
    log.info(
        "Lượt admin: %s",
        f"nạp file {upload.file_name}" if upload else "tin nhắn chữ",
    )
    return {
        "error": None,
        "project_id": None,
        "project_name": None,
        "base_name": None,
        "content_sha256": None,
        "existing_id": None,
        "existing_chunk_count": 0,
        "outcome": None,
        "file_kind": None,
        "documents": None,
        "chunks": None,
        "as_of_date": None,
        "document_id": None,
        "chunk_count": 0,
    }


def choose_branch(state: AdminState) -> str:
    """Trả về "ask_project" hoặc "handle_text" — khớp khoá trong graph.py."""
    return "ask_project" if state.get("upload") else "handle_text"
