"""Đọc ý định tin nhắn.

Hai hàm, hai nơi gọi, hai mức chi phí khác nhau:

  - classify_message: webhook gọi TRƯỚC khi vào graph, để biết mở lượt mới kiểu
    nào. Chỉ bắt keyword nên 0ms, 0đ — không đáng để thành node.
  - parse_free_text_decision: node read_decision gọi, đọc câu trả lời của người
    duyệt. Đây là lời gọi LLM nên phải nằm trong graph mới được trace.

Để chung một file vì cùng trả lời câu "người dùng đang muốn gì", dù nơi gọi khác
nhau.
"""

from functools import lru_cache
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from reminder_agent.config.settings import create_google_genai, load_config
from reminder_agent.prompts.loader import load_prompt
from reminder_agent.utils.schemas import DecisionResult

_REPORT_MARKERS = ("tiếp theo:", "hiện trạng:")


@lru_cache(maxsize=1)
def _approval_reader() -> Runnable:
    """LLM đọc ý người dùng khi họ trả lời bảng đầu việc: duyệt, sửa, hay bỏ.

    Dựng một lần rồi dùng lại. Không để module-level vì lúc đó import sẽ đòi có
    sẵn API key, làm mọi file import module này đều chết theo nếu thiếu .env.
    """
    return create_google_genai(
        load_config()["models"]["decision"], output_schema=DecisionResult
    )


def classify_message(text: str) -> Literal["report", "question"]:
    lowered = text.lower()
    if any(marker in lowered for marker in _REPORT_MARKERS):
        return "report"
    return "question"


async def parse_free_text_decision(text: str) -> dict:
    """Đọc ý định từ câu trả lời tự do của người dùng (bảng đầu việc không có nút)."""
    result = await _approval_reader().ainvoke(
        {
            "system": [SystemMessage(content=load_prompt("decision_system"))],
            "messages": [HumanMessage(content=text)],
        }
    )
    return {"status": result.status, "edit_request": result.edit_request}
