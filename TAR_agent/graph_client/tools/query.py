"""Tool tra bảng lịch công việc. CHỈ ĐỌC, và đọc bằng role đã hạ quyền.

Vỏ @tool mỏng bọc `graph_client/subgraph_sql` — docstring của tool là thứ LLM
đọc để quyết định gọi gì, nên nó viết cho model chứ không phải cho người bảo
trì. Chú thích cho người bảo trì nằm ở đây, ngoài docstring.

`project_id` vào bằng `InjectedState` như `search_docs`, nhưng ở đây nó còn đi
một tầng nữa: `readonly_tx` đặt nó vào `app.project_id` và policy RLS lọc thẳng
dưới Postgres. Lý do phải làm hai tầng — ở `search_docs`, bộ lọc dự án là một
`WHERE` do CODE viết; ở đây model viết CẢ câu SQL, nên bộ lọc mà nằm trong tay
model thì chỉ cần nó quên một lần là bot trả lời câu của dự án này bằng dữ liệu
dự án khác, và câu trả lời trông vẫn rất thật.

Tool trả kèm `sql` — không phải để model đọc mà để log và để soi khi bot trả
lời sai. Con số nào cũng truy ngược được về câu lệnh đã sinh ra nó.
"""

import logging
import uuid
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from TAR_agent.graph_client.subgraph_sql.graph import SQL_GRAPH

log = logging.getLogger(__name__)


@tool
async def query_data(
    question: str,
    project_id: Annotated[uuid.UUID, InjectedState("project_id")],
) -> dict:
    """Tra bảng lịch công việc: đếm, tổng, lọc theo ngày hoặc đơn vị, xếp hạng.

    Bảng có 11 trường: giai đoạn, nhóm, nhóm con, tên công việc, đơn vị, ngày
    bắt đầu, ngày hoàn thành, số ngày, căn cứ pháp lý, kết quả đầu ra, ghi chú
    — hỏi ngoài đó thì dùng search_docs. Mọi câu "bao nhiêu / tổng / liệt kê
    tất cả" phải qua đây; search_docs chỉ trả vài đoạn nên đếm trên đó luôn sai.

    KHÔNG có cột tên dự án — bảng chỉ chứa dữ liệu của ĐÚNG dự án đang hỏi, và
    tên dự án nếu cần đã có sẵn trong ngữ cảnh hội thoại.

    `ghi_chu`, `can_cu_phap_ly`, `ket_qua_dau_ra` là văn xuôi tự do, KHÔNG phải
    cột trạng thái. Câu hỏi "việc nào đang chậm" không lọc được bằng `ghi_chu` —
    lọc bằng ngày rồi đọc `ghi_chu` như chú thích kèm theo. Câu hỏi "việc nào
    chưa có kết quả đầu ra / biên bản nghiệm thu" lọc bằng `ket_qua_dau_ra`
    rỗng.

    Bảng là KẾ HOẠCH DỰ KIẾN: không có trạng thái, không có % hoàn thành,
    không có ngày hoàn thành thực tế. `ngay_bd`/`ngay_ht` là mốc dự kiến, mốc
    đã qua KHÔNG có nghĩa là việc đã xong.

    Trả status "empty" nghĩa là bảng không có dòng nào khớp — nói thẳng với
    người dùng, đừng gọi lại tool này với cách diễn đạt khác.

    Trả status "unsupported" kèm `reason` nghĩa là câu hỏi cần một trường bảng
    KHÔNG CÓ (ví dụ tiến độ, % hoàn thành). Đó là câu trả lời cuối cùng cho ý
    đó — đừng hỏi lại bằng cách diễn đạt khác, và đừng chuyển sang search_docs
    để tìm con số thay thế. Kho tài liệu cũng không có trường đó.

    Args:
        question: câu hỏi về số liệu, viết thành câu đầy đủ ý.
    """
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

    rows = result.get("rows") or []
    sql = result.get("sql") or ""

    # TRƯỚC nhánh `rows` rỗng, và đó là toàn bộ điểm của nó: hai thứ này khác
    # nhau về nghĩa. "empty" = có tra, bảng không có dòng nào khớp.
    # "unsupported" = bảng không có TRƯỜNG để trả lời câu này, nên không có
    # phép đếm nào là đúng cả. Gộp chúng lại thì `compose` nói "kho chưa có tài
    # liệu nào" — một câu vừa sai vừa mời người dùng đi nạp thêm file, trong khi
    # vấn đề là schema không có cột đó.
    if reason := result.get("unsupported"):
        log.info("query_data(%r) -> từ chối: %s", question, reason)
        return {"status": "unsupported", "reason": reason}

    log.info(
        "query_data(%r) -> %d dòng sau %d lượt sinh SQL | %s",
        question, len(rows), result.get("attempt", 0), sql or "(không sinh được)",
    )

    # Khớp khuôn của `search_docs` để tầng trên không phải xử lý hai kiểu
    # payload: `_all_tools_empty` ở graph.py và `render_tool_results` ở
    # compose.py đều đọc `status` trước tiên.
    if not rows:
        return {"status": "empty", "sql": sql}

    return {
        "status": "ok",
        # KHÔNG có `coverage: "partial"` ở đây, và đó là điểm khác duy nhất với
        # `search_docs`: câu SQL đã chạy trên TOÀN BỘ bảng, con số nó trả về là
        # con số đầy đủ chứ không phải phần liên quan nhất.
        "sql": sql,
        "columns": result.get("columns") or [],
        "rows": rows,
    }
