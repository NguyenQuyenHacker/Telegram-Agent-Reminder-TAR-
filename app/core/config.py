from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str
    telegram_chat_id: int | None = None
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""

    database_url: str

    # Chỉ giữ bí mật ở đây. Tham số hành vi của agent (model, temperature,
    # timeout, prompt, số vòng gọi tool) nằm ở reminder_agent/config/models.yaml.
    google_api_key: str

    # Một nhịp duy nhất cho mọi việc chưa xong. Trước đây có bảng 4 nhịp (ưu
    # tiên × trạng thái) nhưng mỗi lượt nhắc giờ chỉ gửi MỘT tin gộp cho cả lô,
    # nên nhịp riêng từng việc không còn nghĩa gì.
    pending_interval_min: int = 30
    # Hoàn tác "đã xong" / "đã hủy" chỉ trong ngần này giờ (R8)
    undo_window_hours: int = 24
    escalation_overdue_days: int = 1
    # Còn ngần này ngày (hoặc ít hơn) tới hạn mà chưa xong thì cũng nâng ưu tiên
    escalation_due_soon_days: int = 3

    reminder_window_start_hour: int = 8
    reminder_window_end_hour: int = 18
    local_timezone: str = "Asia/Ho_Chi_Minh"

    job_a_interval_minutes: int = 1
    # Checkpoint LangGraph cũ hơn ngần này ngày sẽ bị dọn lúc 3h sáng
    checkpoint_retention_days: int = 14


settings = Settings()
