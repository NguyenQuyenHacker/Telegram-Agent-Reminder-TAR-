"""State của graph client.

Cùng nguyên tắc với graph_admin/state.py: không có `chat_id`, node không gửi tin
nhắn mà append vào `outbox`.

`Event` khai lại ở đây chứ không import từ graph_admin — hai package không phụ
thuộc nhau, và hình dạng giống nhau chỉ là tình cờ.

HAI danh sách message, và đây là chỗ dễ sai nhất của cả luồng:

    chat_history   hội thoại với NGƯỜI DÙNG, sống qua mọi lượt.
                   webhook ghi câu hỏi vào đây; `respond` ghi câu trả lời.
    messages       băng làm việc của vòng ReAct, DỌN SẠCH mỗi lượt.
                   `identify_project` ghi câu hỏi vào đây; `tools` ghi
                   ToolMessage; `agent` đọc.

Không tách hai danh sách thì `messages` tích luỹ ToolMessage của mọi lượt cũ, và
agent trộn dữ liệu tra tháng trước với dữ liệu vừa tra — không lỗi nào nổ, câu
trả lời vẫn trôi chảy, chỉ là sai.

`add_messages` là reducer CỘNG DỒN: trả `{"messages": []}` không xoá gì cả. Muốn
dọn phải phát `RemoveMessage(id=…)` cho từng phần tử — xem nodes/reset.py.
"""

import uuid
from typing import Annotated, Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class Event(TypedDict):
    kind: str
    data: dict[str, Any]


class ClientState(TypedDict, total=False):
    # --- hội thoại ---
    chat_history: Annotated[list[AnyMessage], add_messages]
    messages: Annotated[list[AnyMessage], add_messages]

    # --- identify_project điền ---
    # Dự án đang hỏi. BẮT BUỘC khác None trước khi vào `agent` — `search_docs`
    # nhận nó qua InjectedState và không có nhánh nào xử lý None.
    project_id: uuid.UUID | None
    project_name: str | None
    # Nguyên văn tin nhắn cuối của người dùng.
    question_raw: str
    # Câu hỏi sau khi tách tên dự án ra. Chỉ là GỢI Ý trọng tâm cho system prompt
    # của agent — `messages` vẫn mang nguyên văn, nên một lần tách sai không âm
    # thầm làm hỏng truy vấn.
    remaining_question: str | None
    # Câu trả lời cho lượt kết thúc SỚM: chưa chốt được dự án nào, hoặc chốt
    # được rồi mà người dùng chưa hỏi gì để tra. Khác None là `_after_identify`
    # đi thẳng ra `respond`, kể cả khi `project_id` đã có.
    reply: str | None

    # --- guardrail của vòng agent ---
    tool_call_rounds: int
    iteration_count: int
    revise_count: int

    # --- compose điền ---
    answer: str
    verdict: str  # "dat" | "thieu"
    missing: str  # thiếu gì, để agent biết đường tra tiếp

    # --- xuyên suốt ---
    # MÃ lỗi, không phải câu chữ. render.py mới dịch sang tiếng Việt.
    error: str | None
    outbox: list[Event]
