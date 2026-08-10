import hmac

from app.core.config import settings


def verify_webhook_secret(header_value: str | None) -> bool:
    """Telegram gửi lại secret trong header X-Telegram-Bot-Api-Secret-Token."""
    if not settings.telegram_webhook_secret:
        return True
    if not header_value:
        return False
    return hmac.compare_digest(header_value, settings.telegram_webhook_secret)
