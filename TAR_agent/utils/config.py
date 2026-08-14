"""Cấu hình và factory dựng model. Một chỗ duy nhất.

    from TAR_agent.utils.config import settings, AGENT, admin_model, embedding_model

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


# ──────────────────────────── Tham số ────────────────────────────
with (Path(__file__).with_name("models.yaml")).open(encoding="utf-8") as f:
    _YAML = yaml.safe_load(f)

MODELS = _YAML["models"]  # splat thẳng vào ChatGoogleGenerativeAI(**khối)
EMBEDDING = _YAML["embedding"]
AGENT = _YAML["agent"]
CHUNKING = _YAML["chunking"]


# ──────────────────────────── Model ────────────────────────────
# Placeholder chứ không phải chuỗi "{biến}": prompt chứa dấu { } không bị hiểu
# nhầm là biến template.
_PROMPT = ChatPromptTemplate.from_messages(
    [MessagesPlaceholder("system"), MessagesPlaceholder("messages")]
)


def _chat_model(
    params: dict[str, Any],
    tools: list[BaseTool] | None = None,
    output_schema: type[BaseModel] | None = None,
) -> Runnable:
    model = ChatGoogleGenerativeAI(google_api_key=settings.google_api_key, **params)
    if tools:
        model = model.bind_tools(tools)
    if output_schema:
        model = model.with_structured_output(output_schema)
    return _PROMPT | model


def admin_model(tools=None, output_schema=None) -> Runnable:
    """Runnable nhận {"system": [SystemMessage(...)], "messages": [...]}."""
    return _chat_model(MODELS["admin"], tools, output_schema)


def client_model(tools=None, output_schema=None) -> Runnable:
    return _chat_model(MODELS["client"], tools, output_schema)


@lru_cache(maxsize=1)
def embedding_model() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=EMBEDDING["model"], google_api_key=settings.google_api_key
    )


# ──────────────────────────── Prompt ────────────────────────────
_PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _read_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def load_prompt(name: str, **variables: str) -> str:
    """Nạp prompts/<name>.md, thay biến `{{TÊN}}` (không phải str.format)."""
    text = _read_prompt(name)
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


__all__ = [
    "AGENT",
    "CHUNKING",
    "EMBEDDING",
    "EMBEDDING_DIM",
    "MODELS",
    "TZ",
    "admin_model",
    "client_model",
    "embedding_model",
    "load_prompt",
    "now_local",
    "settings",
]
