"""Điểm dừng thứ nhất: file này nạp vào dự án nào.

Node này phải SẠCH TUYỆT ĐỐI. `interrupt()` làm LangGraph chạy lại node từ dòng
1 khi resume, nên mọi tác dụng phụ ở đây sẽ chạy hai lần. Chỉ có thao tác đọc
(đọc lại vô hại) và xử lý giá trị trả về — không ghi DB, không xoá file, không
gửi gì.

Payload đi ra qua `__interrupt__` mà `ainvoke` trả về; tầng Telegram đọc rồi
dựng bàn phím. Lõi vẫn không biết aiogram tồn tại.
"""

import logging
import uuid

from langgraph.types import interrupt

from TAR_agent.graph_admin.state import AdminState
from TAR_agent.utils.projects import list_projects

log = logging.getLogger(__name__)


async def ask_project(state: AdminState) -> dict:
    projects = await list_projects()

    # Kho chưa có dự án nào -> dừng ngay, KHÔNG hỏi. Hiện bàn phím rỗng rồi bắt
    # admin đoán phải làm gì là tệ hơn nói thẳng "gõ /duan <tên> trước".
    if not projects:
        log.info("Từ chối nạp: kho chưa có dự án nào")
        return {"error": "no_projects"}

    upload = state["upload"]
    assert upload is not None  # route đã bảo đảm

    choice = interrupt(
        {
            "kind": "choose_project",
            "data": {
                "file_name": upload.file_name,
                "size_bytes": upload.size_bytes,
                "projects": [
                    {
                        "project_id": str(p.project_id),
                        "name": p.name,
                        "document_count": p.document_count,
                    }
                    for p in projects
                ],
            },
        }
    )

    project_id = uuid.UUID(str(choice["project_id"]))
    name = next((p.name for p in projects if p.project_id == project_id), None)
    if name is None:
        # Dự án bị xoá trong lúc admin còn đang nhìn bàn phím
        return {"error": "project_gone"}

    log.info("Nạp %s vào dự án %s", upload.file_name, name)
    return {"project_id": project_id, "project_name": name}
