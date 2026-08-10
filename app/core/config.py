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

    urgent_pending_interval_min: int = 5
    urgent_snoozed_interval_min: int = 10
    normal_pending_interval_min: int = 30
    normal_snoozed_interval_min: int = 60
    escalation_overdue_days: int = 1

    reminder_window_start_hour: int = 8
    reminder_window_end_hour: int = 18
    local_timezone: str = "Asia/Ho_Chi_Minh"

    job_a_interval_minutes: int = 1
    # Checkpoint LangGraph cũ hơn ngần này ngày sẽ bị dọn lúc 3h sáng
    checkpoint_retention_days: int = 14


settings = Settings()
