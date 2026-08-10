"""Đọc ý định tin nhắn — chạy NGOÀI graph.

Webhook/polling cần biết tin nhắn là báo cáo hay câu hỏi TRƯỚC khi quyết định
đưa vào graph thế nào, và cần đọc câu trả lời của người duyệt để resume graph
đang treo ở interrupt. Cả hai đều xảy ra trước/ngoài luồng graph nên để riêng ở
đây, không phải node.
"""

from functools import lru_cache
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from reminder_agent.config.settings import create_google_genai, load_config
from reminder_agent.prompts.loader import load_prompt
from reminder_agent.utils.schemas import DecisionResult

_REPORT_MARKERS = ("tiếp theo:", "hiện trạng:")


@lru_cache(maxsize=1)
def _approval_reader():
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
