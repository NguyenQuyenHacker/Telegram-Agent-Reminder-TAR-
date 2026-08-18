"""Cấu hình và factory dựng model. Một chỗ duy nhất.

    from TAR_agent.utils.config import settings, load_config, create_google_genai

Bí mật đọc từ .env, tham số hành vi đọc từ models.yaml. File này là chỗ duy
nhất chạm cả hai, và là chỗ duy nhất dựng LLM/embedding.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from pydantic import BaseModel

from persistence.models import EMBEDDING_DIM

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Bắt buộc dùng os.environ[...] (thiếu là KeyError nêu đúng tên biến),
    tuỳ chọn dùng os.getenv(..., mặc định)."""

    # Hai bot, hai token: code luồng client không cầm token có quyền nạp.
    admin_bot_token: str = os.environ["ADMIN_BOT_TOKEN"]
    client_bot_token: str = os.environ["CLIENT_BOT_TOKEN"]
    database_url: str = os.environ["DATABASE_URL"]
    google_api_key: str = os.environ["GOOGLE_API_KEY"]

    # Token riêng chưa đủ — ai biết tên bot cũng nhắn được. Rỗng = không ai dùng
    # được bot admin.
    admin_telegram_ids: str = os.getenv("ADMIN_TELEGRAM_IDS", "")

    # Rỗng = chạy polling thay vì webhook.
    telegram_webhook_url: str = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    # Rỗng = tắt kiểm secret (dev).
    admin_webhook_secret: str = os.getenv("ADMIN_WEBHOOK_SECRET", "")
    client_webhook_secret: str = os.getenv("CLIENT_WEBHOOK_SECRET", "")

    local_timezone: str = os.getenv("LOCAL_TIMEZONE", "Asia/Ho_Chi_Minh")
    checkpoint_retention_days: int = int(os.getenv("CHECKPOINT_RETENTION_DAYS", "14"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "20"))

    @property
    def admin_ids(self) -> frozenset[int]:
        return frozenset(
            int(raw) for raw in self.admin_telegram_ids.split(",") if raw.strip()
        )


settings = Settings()

TZ = ZoneInfo(settings.local_timezone)


def now_local() -> datetime:
    return datetime.now(TZ)


_CONFIG_PATH = Path(__file__).with_name("models.yaml")


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Nguyên nội dung models.yaml. Đọc đĩa một lần, sau đó lấy từ cache.

    Trả về CHÍNH dict trong cache chứ không phải bản sao: sửa nó là sửa cho cả
    tiến trình, vĩnh viễn, không có gì báo. Nơi gọi chỉ đọc.
    """
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


# Placeholder chứ không phải chuỗi "{biến}": prompt chứa dấu { } không bị hiểu
# nhầm là biến template.
_PROMPT = ChatPromptTemplate.from_messages(
    [MessagesPlaceholder("system"), MessagesPlaceholder("messages")]
)


def create_google_genai(
    chat_config: dict[str, Any],
    *,
    tools: list[BaseTool] | None = None,
    output_schema: type[BaseModel] | None = None,
) -> Runnable:
    """Một khối `models:` trong yaml -> Runnable nhận
    `{"system": [SystemMessage(...)], "messages": [...]}`.

    Nhận thẳng KHỐI CẤU HÌNH chứ không nhận tên khối: luồng client có sáu chỗ
    gọi LLM, mỗi chỗ một khối, và nơi dựng graph đã cầm sẵn `config["models"]`
    rồi. Truyền tên là thêm một lớp tra bảng chỉ để tra ngược lại đúng cái dict
    vừa có trong tay.

    `tools` và `output_schema` là keyword-only: `create_google_genai(cfg, X)`
    không đọc ra được X là tool hay schema.
    """
    model = ChatGoogleGenerativeAI(google_api_key=settings.google_api_key, **chat_config)
    if tools:
        model = model.bind_tools(tools)
    if output_schema:
        model = model.with_structured_output(output_schema)
    return _PROMPT | model


@lru_cache(maxsize=1)
def embedding_model() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=load_config()["embedding"]["model"],
        google_api_key=settings.google_api_key,
    )


_PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _read_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def load_prompt(name: str, **variables: str) -> str:
    """Nạp prompts/<name>.md, thay biến `{{TÊN}}` (không phải str.format).

    LUẬT khi viết file .md: đặt mọi `{{BIẾN}}` ở CUỐI prompt. Gemini cache ngầm
    theo tiền tố giống nhau ĐÚNG TỪNG BYTE, nên một `{{TODAY}}` ở dòng 3 làm cả
    9KB luật phía sau nó không bao giờ trúng cache — mỗi ngày một tiền tố mới,
    mỗi câu hỏi một tiền tố mới.
    """
    text = _read_prompt(name)
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


__all__ = [
    "EMBEDDING_DIM",
    "TZ",
    "create_google_genai",
    "embedding_model",
    "load_config",
    "load_prompt",
    "now_local",
    "settings",
]
