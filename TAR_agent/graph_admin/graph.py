"""Nối dây graph admin. Chỉ nối — logic nằm ở nodes/ và utils/.

                              ┌──── unchanged / cancelled / lỗi ────┐
                              │                                      ▼
    START ─ route ─┬─ ask_project ─ check_file ─ parse ─ extract ─ store ─ report ─ END
                   │       │            │           │                        ▲
                   │       └── lỗi ─────┴───────────┘                        │
                   └─ handle_text ──────────────────────────────────────────-┘

               interrupt #1: chọn dự án     interrupt #2: xác nhận ghi đè

Vì sao `ask_project` đứng TRƯỚC `check_file` và `parse`:
  - `check_file` tra theo (project_id, file_name) nên phải biết dự án trước.
  - `parse` là khâu tốn nhất trước embedding. Đặt nó sau cả hai điểm dừng thì
    không bao giờ đọc một file mà admin sắp bấm Huỷ.

`extract` là node DUY NHẤT của nhánh nạp không có cạnh về `report`. Trích hỏng
thì đi tiếp sang `store` với 0 dòng: tài liệu vẫn có chunk, vẫn tra được bằng
`search_docs` — huỷ cả lượt nạp là mất luôn phần đang chạy tốt để trừng phạt
phần mới. `report` đọc `extract_error` để nói rõ.

Không có node kiểm định dạng: app/telegram/download.py đã chặn đuôi lạ và file
quá nặng trước khi dựng UploadedFile. Kiểm lại ở đây chỉ là code chết.

`report` là cửa ra duy nhất của nhánh nạp — mọi đường đều đổ về đó, nên không
lượt nào kết thúc mà admin không nhận được gì, và không đường nào bỏ sót việc
xoá file tạm.

Nhánh nạp CÓ gọi LLM, ở đúng một chỗ: node `extract` (1 lượt mỗi sheet .xlsx,
hoặc N/batch_chunks lượt với .txt). Trước khi có nó, chi phí một lượt nạp chỉ
là vài lượt ghi checkpoint cộng một lượt embedding — không còn đúng nữa, và
admin phải chờ lâu hơn hẳn. Đổi lại: câu hỏi "còn bao nhiêu việc chậm" trả lời
được, thứ mà `k = 5` đoạn của retriever không bao giờ làm nổi.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from TAR_agent.graph_admin.nodes import (
    Extract,
    ask_project,
    check_file,
    handle_text,
    parse,
    report,
    route,
)
from TAR_agent.graph_admin.nodes import store as store_node
from TAR_agent.graph_admin.nodes.check_file import choose_branch as after_check
from TAR_agent.graph_admin.nodes.parse import choose_branch as after_parse
from TAR_agent.graph_admin.nodes.route import choose_branch as after_route
from TAR_agent.graph_admin.state import AdminState


def build_admin_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(AdminState)

    builder.add_node("route", route)
    builder.add_node("ask_project", ask_project)
    builder.add_node("check_file", check_file)
    builder.add_node("parse", parse)
    builder.add_node("extract", Extract())
    builder.add_node("store", store_node)
    builder.add_node("report", report)
    builder.add_node("handle_text", handle_text)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route", after_route, {"ask_project": "ask_project", "handle_text": "handle_text"}
    )

    # ask_project chỉ hỏng ở đúng một chỗ (kho chưa có dự án nào) nên dùng
    # điều kiện tại chỗ, không cần thêm một hàm choose_branch nữa.
    builder.add_conditional_edges(
        "ask_project",
        lambda state: "report" if state.get("error") else "check_file",
        {"report": "report", "check_file": "check_file"},
    )
    builder.add_conditional_edges(
        "check_file", after_check, {"parse": "parse", "report": "report"}
    )
    # `after_parse` vẫn rẽ đúng hai đường như cũ; chỉ có đích của nhánh thành
    # công đổi từ `store` sang `extract`.
    builder.add_conditional_edges(
        "parse", after_parse, {"store": "extract", "report": "report"}
    )

    # KHÔNG có conditional ở đây: `extract` không bao giờ rẽ về `report`.
    builder.add_edge("extract", "store")
    builder.add_edge("store", "report")
    builder.add_edge("report", END)
    builder.add_edge("handle_text", END)

    return builder.compile(checkpointer=checkpointer)
