"""Tool của agent admin — RỖNG tới khi dựng vòng ReAct.

Sẽ giữ: list_documents, delete_document, kb_stats.

Hai luật đặt trước:
  - Tool GHI chỉ được có ở đây. graph_client/tools không import được sang đây.
  - `delete_document` nhận `document_id` chứ không nhận tên: đoán nhầm một cái
    tên là xoá nhầm một file, mà xoá không hoàn lại được.
"""

ADMIN_TOOLS: list = []

__all__ = ["ADMIN_TOOLS"]
