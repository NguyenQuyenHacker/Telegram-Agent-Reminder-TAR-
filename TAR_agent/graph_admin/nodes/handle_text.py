"""Nhánh tin nhắn chữ, và là chỗ DUY NHẤT tạo dự án. Không gọi LLM.

    /duan <tên>   -> tạo dự án
    /duan         -> liệt kê dự án kèm số tài liệu
    còn lại       -> câu hướng dẫn

Lúc nạp file không bao giờ tự tạo dự án: một caption gõ nhầm sẽ sinh ra dự án
rác đã có tài liệu nằm trong, không ai thấy tới lúc client hỏi mà không tra ra.
"""

import logging

from langchain_core.messages import HumanMessage

from TAR_agent.graph_admin.state import AdminState
from TAR_agent.utils.projects import create_project, list_projects

log = logging.getLogger(__name__)

_COMMAND = "/duan"


def _last_text(state: AdminState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content).strip()
    return ""


async def handle_text(state: AdminState) -> dict:
    text = _last_text(state)

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
