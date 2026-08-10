"""Đọc models.yaml và dựng LLM từ đó.

    config = load_config()
    llm = create_google_genai(config["models"]["agent"], tools=...)
    await llm.ainvoke({"system": [SystemMessage(...)], "messages": [...]})

Khối yaml được splat vào ChatGoogleGenerativeAI(**chat_config) nên tên khoá phải
đúng tên tham số của lớp đó. API key khai ở app/core/config.py.
"""

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from app.core.config import settings

_CONFIG_PATH = Path(__file__).with_name("models.yaml")


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Toàn bộ models.yaml. Cache một lần vì file không đổi lúc chạy."""
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# Placeholder chứ không phải chuỗi "{biến}": prompt chứa dấu { } không bị hiểu
# nhầm là biến template, và prompt động truyền được lúc gọi.
_PROMPT = ChatPromptTemplate.from_messages(
    [MessagesPlaceholder("system"), MessagesPlaceholder("messages")]
)


def create_google_genai(
    chat_config: dict[str, Any],
    *,
    tools: Sequence[BaseTool] | None = None,
    output_schema: type[BaseModel] | None = None,
) -> Runnable:
    """LLM của một vai: prompt -> model.

    Trả về Runnable nhận `{"system": [SystemMessage(...)], "messages": [...]}`.
    """
    model = ChatGoogleGenerativeAI(
        google_api_key=settings.google_api_key, **chat_config
    )
    if tools is not None:
        model = model.bind_tools(tools)
    if output_schema is not None:
        model = model.with_structured_output(output_schema)
    return _PROMPT | model
