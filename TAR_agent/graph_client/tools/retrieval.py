"""Tra cứu HỢP NHẤT: bảng số liệu và tài liệu, chạy SONG SONG, trả MỘT payload.

Thay cho cặp `search_docs` / `query_data` cũ. Lý do gộp: cả hai nhánh đều hỏng
theo kiểu ĐỌC ĐƯỢC từ payload (bảng trả 0 dòng hoặc `unsupported`; tài liệu trả
rỗng hoặc `coverage: "partial"`), nên chạy cả hai rồi để `agent` và `compose`
nhìn đủ hai bên thì mọi ca một nhánh hỏng đều có nhánh kia đỡ — không ai phải
đoán trước câu hỏi thuộc loại nào. Việc cắt phần lạc đề là của node `agent`.

PAYLOAD PHẲNG, không lồng `{"table": ..., "docs": ...}`: `grounding._tool_text`,
`compose.render_tool_results` và `agent.number_evidence` đều đọc `passages` và
`rows` từ CÙNG MỘT dict. Lồng vào thì cả ba im lặng đọc ra rỗng.
"""

import asyncio
import logging
import uuid
from typing import Any

from TAR_agent.graph_client.subgraph.graph import SEARCH_GRAPH
from TAR_agent.graph_client.subgraph_sql.graph import SQL_GRAPH
from TAR_agent.utils.timing import track

log = logging.getLogger(__name__)


async def _query_table(question: str, project_id: uuid.UUID) -> dict[str, Any]:
    """Nhánh SQL. Không bao giờ ném — hỏng thì trả trạng thái đọc được."""
    # Đo RIÊNG từng nhánh: `retrieve` là `max(hai nhánh)` nên chỉ có con số tách
    # ra mới biết nhánh nào là đuôi, tức là nhánh nào đáng tối ưu.
    async with track("retrieve.table") as span:
        result = await SQL_GRAPH.ainvoke(
            {
                "question": question,
                "project_id": project_id,
                "sql": "",
                "rows": [],
                "columns": [],
                "error": None,
                "attempt": 0,
                "ok": False,
                "unsupported": None,
            }
        )
        span["attempt"] = result.get("attempt", 0)
        span["rows"] = len(result.get("rows") or [])

    sql = result.get("sql") or ""
    rows = result.get("rows") or []

    # Kiểm TRƯỚC nhánh `rows` rỗng: "không có dòng nào khớp" khác hẳn "bảng
    # không có TRƯỜNG để trả lời". Gộp lại thì compose mời người dùng nạp thêm
    # file, trong khi nạp bao nhiêu cũng không sinh ra cột đó.
    if reason := result.get("unsupported"):
        log.info("retrieve_all · bảng: từ chối — %s", reason)
        return {"table": "unsupported", "table_reason": reason}

    log.info(
        "retrieve_all · bảng: %d dòng sau %d lượt sinh SQL",
        len(rows),
        result.get("attempt", 0),
    )
    if not rows:
        return {"table": "empty", "sql": sql}

    return {
        "table": "ok",
        "sql": sql,
        "columns": result.get("columns") or [],
        "rows": rows,
    }


async def _search_docs(question: str, project_id: uuid.UUID) -> dict[str, Any]:
    """Nhánh tài liệu. Không bao giờ ném — hỏng thì trả trạng thái đọc được."""
    async with track("retrieve.docs") as span:
        result = await SEARCH_GRAPH.ainvoke(
            {
                "question": question,
                "original": question,
                "project_id": project_id,
                "attempt": 0,
                "docs": [],
                "ok": False,
            }
        )
        span["attempt"] = result.get("attempt", 0)
        span["docs"] = len(result.get("docs") or [])

    docs = result.get("docs") or []
    log.info(
        "retrieve_all · tài liệu: %d đoạn sau %d lượt tra",
        len(docs),
        result.get("attempt", 0),
    )
    if not docs:
        return {"docs": "empty"}

    return {
        "docs": "ok",
        # Retriever lấy `k` đoạn liên quan nhất, KHÔNG BAO GIỜ lấy hết.
        # `compose.md` có một luật riêng dựa vào cờ này.
        "coverage": "partial",
        "passages": [
            {
                "content": doc.content,
                "file_name": doc.file_name,
                "as_of_date": doc.as_of_date.isoformat(),
                "heading_path": doc.heading_path,
            }
            for doc in docs
        ],
    }


def _branch_error(branch: str, exc: BaseException) -> dict[str, Any]:
    """Một nhánh ném thì ghi log và trả trạng thái, KHÔNG kéo nhánh kia chết theo."""
    log.exception("retrieve_all · nhánh %s hỏng", branch, exc_info=exc)
    return {branch: "error"}


async def retrieve_all(question: str, project_id: uuid.UUID) -> dict[str, Any]:
    """Tra cả bảng lịch công việc lẫn kho tài liệu, trả về một payload phẳng.

    `status` ở gốc chỉ dùng cho `ClientGraph._all_tools_empty`: "empty" nghĩa là
    CẢ HAI nhánh không có gì — chỉ lúc đó tra lại mới chắc chắn vô ích.
    """
    table, docs = await asyncio.gather(
        _query_table(question, project_id),
        _search_docs(question, project_id),
        return_exceptions=True,
    )
    if isinstance(table, BaseException):
        table = _branch_error("table", table)
    if isinstance(docs, BaseException):
        docs = _branch_error("docs", docs)

    payload: dict[str, Any] = {**table, **docs}
    has_data = bool(payload.get("rows")) or bool(payload.get("passages"))
    payload["status"] = "ok" if has_data else "empty"
    return payload
