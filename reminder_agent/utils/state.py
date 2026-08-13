from typing import Annotated, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# Phải là TypedDict của typing_extensions: pydantic không dựng được schema từ
# typing.TypedDict trên Python < 3.12, và langgraph cần schema đó khi introspect
# graph (get_graph(), LangGraph Studio).
from typing_extensions import TypedDict


# Bản TypedDict của schemas.ExtractedTask: state chứa dict (đã model_dump) chứ
# không chứa object pydantic, để checkpoint serialize được gọn.
class ExtractedTask(TypedDict):
    group: str
    content: str
    due_date: str | None
    priority: Literal["urgent", "normal"]


class GraphState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    chat_id: int
    raw_report_text: str | None
    extracted_tasks: list[ExtractedTask]
    edit_request: str | None
    # Nguyên văn câu trả lời của người duyệt, ask_confirm ghi -> read_decision đọc
    confirm_reply: str | None
    confirm_status: Literal["approved", "edit", "abandoned", "unclear"] | None
    tool_call_rounds: int
    # Đề xuất cập nhật do propose_task_update / propose_subtask_update sinh ra,
    # chờ người dùng duyệt. apply_updates ghi xong thì dọn về rỗng.
    pending_updates: list[dict]
    # Bảng chi tiết việc con do get_task_detail trả về. Không phải đề xuất — chỉ
    # là thứ để hiển thị — nên answer() gửi xong là dọn ngay, không đi qua chốt
    # duyệt nào.
    pending_views: list[dict]
    update_reply: str | None
    update_status: Literal["approved", "abandoned", "unclear"] | None
    # Việc vừa lưu mà chưa có hạn: câu trả lời kế tiếp của người dùng nhiều khả
    # năng là hạn cho chúng. Tự cạn dần khi việc đã có hạn.
    awaiting_due_task_ids: list[str]
