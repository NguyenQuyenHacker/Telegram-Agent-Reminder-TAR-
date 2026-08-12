import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.services.reminder_service import run_job_a
from persistence.proc.checkpoints import purge_old_checkpoints

log = logging.getLogger(__name__)

# 3h sáng: ngoài khung giờ nhắc việc nên không đụng Job A
_PURGE_HOUR = 3


async def _purge_checkpoints() -> None:
    """Dọn checkpoint cũ. Lỗi thì ghi log rồi thôi — không được làm chết scheduler."""
    try:
        await asyncio.to_thread(purge_old_checkpoints, settings.checkpoint_retention_days)
    except Exception:
        log.exception("PURGE: dọn checkpoint thất bại")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.local_timezone)
    scheduler.add_job(
        run_job_a,
        "interval",
        minutes=settings.job_a_interval_minutes,
        id="job_a",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _purge_checkpoints,
        "cron",
        hour=_PURGE_HOUR,
        id="purge_checkpoints",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
