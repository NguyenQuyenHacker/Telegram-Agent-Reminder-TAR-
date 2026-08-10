import logging

from aiogram import Bot

from app.core.config import settings

log = logging.getLogger(__name__)

bot = Bot(token=settings.bot_token)

WEBHOOK_PATH = "/webhooks/telegram"


async def set_webhook() -> None:
    if not settings.telegram_webhook_url:
        log.info("Chưa cấu hình TELEGRAM_WEBHOOK_URL, bỏ qua setWebhook (dev local)")
        return
    url = settings.telegram_webhook_url.rstrip("/") + WEBHOOK_PATH
    await bot.set_webhook(
        url=url,
        secret_token=settings.telegram_webhook_secret or None,
        drop_pending_updates=True,
    )
    log.info("Đã đăng ký webhook: %s", url)


async def delete_webhook() -> None:
    if settings.telegram_webhook_url:
        await bot.delete_webhook()
