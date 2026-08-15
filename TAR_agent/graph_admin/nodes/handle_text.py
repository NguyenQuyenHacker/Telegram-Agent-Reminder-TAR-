"""Nhánh tin nhắn chữ, và là chỗ DUY NHẤT tạo dự án. Không gọi LLM.

    /duan <tên>     -> tạo dự án
    /duan           -> liệt kê dự án kèm số tài liệu
    /reindex <tên>  -> xoá dấu vân tay nội dung, cho phép nạp lại file cũ
    còn lại         -> câu hướng dẫn

Lúc nạp file không bao giờ tự tạo dự án: một caption gõ nhầm sẽ sinh ra dự án
rác đã có tài liệu nằm trong, không ai thấy tới lúc client hỏi mà không tra ra.
"""

import asyncio
import logging

from langchain_core.messages import HumanMessage

from TAR_agent.graph_admin.state import AdminState
from TAR_agent.utils.projects import create_project, list_projects
from TAR_agent.utils.text import name_uuid
from persistence.proc import documents as doc_proc

log = logging.getLogger(__name__)

_COMMAND = "/duan"
_REINDEX = "/reindex"


def _last_text(state: AdminState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content).strip()
    return ""


async def _reindex(name: str) -> dict:
    """Gỡ chốt hash để admin nạp lại file cũ, chạy chúng qua node `extract`.

    `check_file` trả `unchanged` khi `sha256` trùng, và nó chặn TRƯỚC `parse` —
    nên tài liệu nạp từ thời chưa có `extract` không có đường nào chạy qua nó,
    kể cả khi admin gửi lại đúng file đó. Đây là chỗ mở đường.

    KHÔNG tự trích lại từ DB: cột `source_document.documents` chỉ giữ đầu ra
    loader (text), không giữ lưới ô, nên `.xlsx` không dựng lại được. Admin vẫn
    phải gửi lại file — lệnh này chỉ làm cho lần gửi đó không bị bỏ qua.
    """
    if not name:
        return {"outbox": [{"kind": "reindex_usage", "data": {}}]}

    count = await asyncio.to_thread(doc_proc.reset_hashes, name_uuid(name))
    log.info("Gỡ hash %d tài liệu của dự án %r", count, name)
    return {
        "outbox": [
            {"kind": "reindex_done", "data": {"name": name, "document_count": count}}
        ]
    }


async def handle_text(state: AdminState) -> dict:
    text = _last_text(state)

    if text.lower().startswith(_REINDEX):
        return await _reindex(text[len(_REINDEX) :].strip())

    if not text.lower().startswith(_COMMAND):
        return {"outbox": [{"kind": "hint", "data": {}}]}

    name = text[len(_COMMAND) :].strip()

    if not name:
        projects = await list_projects()
        return {
            "outbox": [
                {
                    "kind": "project_list",
                    "data": {
                        "projects": [
                            {"name": p.name, "document_count": p.document_count}
                            for p in projects
                        ]
                    },
                }
            ]
        }

    try:
        project, created = await create_project(name)
    except ValueError:
        # Tên chỉ có emoji hoặc dấu câu -> chuẩn hoá ra chuỗi rỗng
        return {"outbox": [{"kind": "project_invalid_name", "data": {"name": name}}]}

    log.info("%s dự án: %s", "Tạo" if created else "Đã có", project.name)
    return {
        "outbox": [
            {
                "kind": "project_created" if created else "project_exists",
                "data": {"name": project.name},
            }
        ]
    }
