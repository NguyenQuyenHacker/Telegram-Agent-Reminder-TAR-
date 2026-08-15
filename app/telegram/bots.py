"""Hai bot Telegram, hai vai.

  admin_bot  (TAR_admin_bot)  — nạp và quản lý tài liệu
  client_bot (TAR_Client_bot) — hỏi đáp, không có đường nào ghi vào kho

Tách token chứ không dùng một bot phân quyền: người dùng thường không nhìn thấy
bot admin, và code luồng client không cầm token có quyền nạp. Chốt quyền theo
user id vẫn phải có (app/core/security.is_admin) — ai biết tên bot cũng nhắn
cho nó được.
"""

from dataclasses import dataclass

from aiogram import Bot

from TAR_agent.utils.config import settings

ADMIN_WEBHOOK_PATH = "/webhooks/telegram/admin"
CLIENT_WEBHOOK_PATH = "/webhooks/telegram/client"


@dataclass(frozen=True)
class BotRole:
    name: str
    bot: Bot
    webhook_path: str
    webhook_secret: str


admin_bot = Bot(token=settings.admin_bot_token)
client_bot = Bot(token=settings.client_bot_token)

ADMIN = BotRole("admin", admin_bot, ADMIN_WEBHOOK_PATH, settings.admin_webhook_secret)
CLIENT = BotRole("client", client_bot, CLIENT_WEBHOOK_PATH, settings.client_webhook_secret)

ALL_ROLES = (ADMIN, CLIENT)


async def set_webhooks() -> None:
    """Đăng ký webhook cho cả hai bot. Chưa có domain public thì bỏ qua."""
    if not settings.telegram_webhook_url:
        return

    base = settings.telegram_webhook_url.rstrip("/")
    for role in ALL_ROLES:
        await role.bot.set_webhook(
            url=base + role.webhook_path,
            secret_token=role.webhook_secret or None,
            drop_pending_updates=True,
        )


async def close_bots() -> None:
    for role in ALL_ROLES:
        await role.bot.session.close()
