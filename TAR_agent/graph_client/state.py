"""State của graph client. Node không gửi tin nhắn, chúng append vào `outbox`.

HAI danh sách message, và đây là chỗ dễ sai nhất của cả luồng:

    chat_history   hội thoại với NGƯỜI DÙNG, sống qua mọi lượt. Danh sách DUY
                   NHẤT còn đi tới một model (`identify_project`).
    messages       kết quả tra của LƯỢT NÀY, dọn sạch mỗi lượt. Không tách thì
                   `compose` trộn dữ liệu tra tháng trước với dữ liệu vừa tra —
                   không lỗi nào nổ, câu trả lời vẫn trôi chảy, chỉ là sai.

`messages` không đi tới model như một băng hội thoại: `agent` và `compose` đọc
nó qua khối văn bản do CODE dựng, nên nó không phải giữ đúng cặp
AIMessage/ToolMessage mà Gemini soi.

`add_messages` là reducer CỘNG DỒN: trả `{"messages": []}` không xoá gì cả —
xem nodes/reset.py.

`Event` khai lại ở đây chứ không import từ graph_admin: hai package không phụ
thuộc nhau, hình dạng giống nhau chỉ là tình cờ.
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
    # BẮT BUỘC khác None trước khi vào `retrieve`: `retrieve_all` truyền thẳng nó
    # xuống hai subgraph và không có nhánh nào xử lý None.
    project_id: uuid.UUID | None
    project_name: str | None
    # Nguyên văn tin nhắn cuối của người dùng.
    question_raw: str
    # Câu hỏi sau khi tách tên dự án ra. KHÔNG dùng để tra — giữ lại vì nó cho
    # biết `identify_project` đã HIỂU câu hỏi thành gì, manh mối đầu tiên khi bot
    # chọn nhầm dự án.
    remaining_question: str | None
    # Câu trả lời cho lượt kết thúc SỚM. Khác None là `_after_identify` đi thẳng
    # ra `respond`, kể cả khi `project_id` đã có.
    reply: str | None

    # --- agent điền ---
    # Kết luận của node `agent`, thứ DUY NHẤT `_after_agent` đọc:
    #   "retrieve"  đi tra kho
    #   "answer"    trả lời thẳng, `reply` đã có chữ
    #   "compose"   đã chọn lọc xong, `evidence` đã có nguồn
    agent_action: str
    # Câu đem đi tra. `retrieve` đọc nó chứ không đọc `question_raw`.
    query: str
    # Khối nguồn ĐÃ LỌC, dựng sẵn cho prompt của `compose`. KHÔNG thay thế
    # `messages`: `grounding.check` vẫn chấm theo lô GỐC ở đó.
    evidence: str
    # Một hai câu dặn `compose` câu hỏi đang hỏi gì. Không phải câu trả lời.
    outline: str

    # --- guardrail của vòng retrieve ⇄ compose ---
    iteration_count: int
    revise_count: int

    # --- compose điền ---
    answer: str
    verdict: str  # "ok" | "insufficient"
    missing: str  # thiếu gì — `retrieve` ghép vào truy vấn của vòng sau

    # --- xuyên suốt ---
    # MÃ lỗi, không phải câu chữ. render.py mới dịch sang tiếng Việt.
    error: str | None
    outbox: list[Event]
