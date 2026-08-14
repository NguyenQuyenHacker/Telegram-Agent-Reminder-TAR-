"""Embed rồi ghi vào kho. Node duy nhất trong nhánh nạp có tác dụng phụ.

Tới đây thì đã chắc chắn: admin đã chọn dự án, đã đồng ý ghi đè (nếu cần), file
đã đọc được. Không còn gì để hỏi, chỉ còn tốn tiền.

`writer.save` gói cả xoá-chunk-cũ, ghi tài liệu và ghi chunk mới vào MỘT
transaction — xem docstring của nó để biết vì sao không tách được.
"""

import asyncio
import logging

from TAR_agent.graph_admin.state import AdminState
from TAR_agent.graph_admin.helpers import writer
from TAR_agent.utils.embed import embed_documents

log = logging.getLogger(__name__)


async def store(state: AdminState) -> dict:
    chunks = state["chunks"]
    project_id = state["project_id"]
    assert project_id is not None

    try:
        embeddings = await embed_documents([c.page_content for c in chunks])
    except Exception:
        log.exception("Embedding hỏng: %s", state["base_name"])
        return {"error": "embed_failed"}

    try:
        stored = await asyncio.to_thread(
            writer.save,
            project_id=project_id,
            file_name=state["base_name"],
            file_kind=state["file_kind"],
            content_sha256=state["content_sha256"],
            as_of_date=state["as_of_date"],
            documents=state["documents"],
            chunks=chunks,
            embeddings=embeddings,
            uploaded_by=state.get("uploaded_by", 0),
        )
    except Exception:
        log.exception("Ghi DB hỏng: %s", state["base_name"])
        return {"error": "write_failed"}

    log.info(
        "Đã ghi %s: %d chunk (thay %d chunk cũ)",
        state["base_name"], stored.chunk_count, stored.replaced_chunks,
    )
    return {"document_id": stored.document_id, "chunk_count": stored.chunk_count}
