"""Cửa ra DUY NHẤT của mọi lượt. Không gọi LLM.

Ba đường đổ về đây:
    identify_project → respond   không xác định được dự án, câu hỏi về kho, hoặc
                                 đã chốt dự án mà người dùng chưa hỏi gì (luật 8)
    compose → respond            đã có câu trả lời
    (nhánh lỗi)                  `error` khác None

Và đây cũng là chỗ DUY NHẤT ghi câu trả lời vào `chat_history`. Rải việc đó ra
nhiều node là sớm muộn có đường ra quên ghi, rồi lượt sau bot không nhớ nó vừa
nói gì — mà biểu hiện là "bot hỏi lại đúng câu nó vừa hỏi".

Thân node là `ClientGraph._respond`; ở đây là hàm thuần dựng event.
"""

from TAR_agent.graph_client.state import ClientState, Event

# Chạm trần vòng lặp: có câu trả lời nhưng chưa qua được vòng tự rà soát. Gửi
# vẫn hơn im lặng, nhưng phải nói rõ để người đọc tự kiểm lại.
HEDGE = "\n\n(Mình chưa kiểm chứng hết được phần này — bạn đối chiếu lại tài liệu gốc giúp mình.)"


def answer_event(text: str, project_name: str | None = None) -> Event:
    """`project_name` đi kèm để tầng Telegram dựng dòng tiêu đề.

    Gửi TÊN chứ không phải câu đã ghép sẵn: lõi không biết Telegram hiển thị thế
    nào, và đó là ranh giới duy nhất giữ được render.py là chỗ duy nhất biết.
    None ở lượt kết thúc sớm (chưa chốt được dự án nào) — khi đó không có tiêu đề.
    """
    return {
        "kind": "answer",
        "data": {"text": text, "project_name": project_name},
    }


def failed_event(reason: str) -> Event:
    return {"kind": "client_failed", "data": {"reason": reason}}


def outgoing_text(state: ClientState) -> str:
    """Chữ sẽ gửi đi: `reply` của lượt kết thúc sớm, hoặc `answer` của compose."""
    return (state.get("reply") or state.get("answer") or "").strip()
