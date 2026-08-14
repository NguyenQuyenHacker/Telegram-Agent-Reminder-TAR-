"""State của graph client.

Cùng nguyên tắc với graph_admin/state.py: không có `chat_id`, node không gửi tin
nhắn mà append vào `outbox`.

`Event` khai lại ở đây chứ không import từ graph_admin — hai package không phụ
thuộc nhau, và hình dạng giống nhau chỉ là tình cờ.
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
    messages: Annotated[list[AnyMessage], add_messages]
    # Dự án đang hỏi. BẮT BUỘC có trước khi tra.
    project_id: uuid.UUID | None
    retrieved: list[Any]  # list[RetrievedChunk]
    tool_call_rounds: int
    outbox: list[Event]
