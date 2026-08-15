"""Dọn state của lượt trước. Chạy đầu MỌI lượt, không gọi LLM, không I/O.

Các khoá ngoài `messages`/`chat_history` không có reducer nên LangGraph giữ
nguyên giá trị cũ nếu không ai ghi đè. Không dọn thì một chat từng dính
`error="llm_failed"` mang đúng lỗi đó sang mọi lượt sau, và `verdict="thieu"`
của lượt trước đá lượt mới ngược về agent trước khi nó kịp tra gì.

`messages` phải dọn bằng `RemoveMessage(id=…)` TỪNG PHẦN TỬ: `add_messages` là
reducer cộng dồn, trả `{"messages": []}` không xoá gì cả. `REMOVE_ALL_MESSAGES`
là của bản langgraph mới hơn, KHÔNG có ở 0.2.60 (bản đang ghim).
"""

from langchain_core.messages import RemoveMessage

from TAR_agent.graph_client.state import ClientState


def reset(state: ClientState) -> dict:
    """KHÔNG đụng `project_id`, `project_name`, `chat_history`.

    Ba thứ đó là trí nhớ giữa các lượt: dọn `project_id` ở đây thì mọi tin nhắn
    cộc lốc ("còn phần điện thì sao") đều bị hỏi lại tên dự án. Việc XOÁ dự án
    khi người dùng gõ /start hay /huy là của `identify_project`, có chủ đích,
    không phải hệ quả của một node dọn dẹp.
    """
    return {
        "messages": [RemoveMessage(id=m.id) for m in state.get("messages", [])],
        "tool_call_rounds": 0,
        "iteration_count": 0,
        "revise_count": 0,
        "outbox": [],
        "error": None,
        "verdict": "",
        "missing": "",
        "answer": "",
        "reply": None,
        "remaining_question": None,
    }
