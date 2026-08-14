import hmac

from TAR_agent.utils.config import settings


def verify_webhook_secret(header_value: str | None, expected: str) -> bool:
    """Telegram gửi lại secret trong header X-Telegram-Bot-Api-Secret-Token.

    `expected` truyền vào chứ không đọc thẳng từ settings: hai bot có hai secret
    riêng, endpoint nào kiểm secret của endpoint đó.
    """
    if not expected:
        return True
    if not header_value:
        return False
    return hmac.compare_digest(header_value, expected)


def is_admin(user_id: int | None) -> bool:
    """Chốt quyền của luồng admin.

    Danh sách rỗng thì KHÔNG ai qua được — thà bot admin câm còn hơn mở cho cả
    thiên hạ vì quên điền một biến môi trường.
    """
    return user_id is not None and user_id in settings.admin_ids
