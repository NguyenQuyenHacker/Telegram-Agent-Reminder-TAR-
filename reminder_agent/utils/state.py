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
