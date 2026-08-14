import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from TAR_agent.utils.config import settings
from persistence.proc.checkpoints import purge_old_checkpoints

log = logging.getLogger(__name__)

# 3h sáng: giờ không ai nhắn cho bot, dọn dẹp không giành tài nguyên với ai
_PURGE_HOUR = 3


async def _purge_checkpoints() -> None:
    """Dọn checkpoint cũ. Lỗi thì ghi log rồi thôi — không được làm chết scheduler."""
    try:
        await asyncio.to_thread(purge_old_checkpoints, settings.checkpoint_retention_days)
    except Exception:
        log.exception("PURGE: dọn checkpoint thất bại")


def build_scheduler() -> AsyncIOScheduler:
    """Chỉ còn một việc nền: dọn checkpoint LangGraph.

    Job A (quét việc tới hạn rồi gửi tin nhắc) đã bị bỏ hẳn cùng chức năng nhắc
    việc. Scheduler giữ lại vì bảng checkpoint vẫn phình theo mỗi lượt chạy graph
    và không ai tự dọn.
    """
    scheduler = AsyncIOScheduler(timezone=settings.local_timezone)
    scheduler.add_job(
        _purge_checkpoints,
        "cron",
        hour=_PURGE_HOUR,
        id="purge_checkpoints",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
