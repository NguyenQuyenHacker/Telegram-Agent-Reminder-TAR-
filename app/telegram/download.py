"""Tải file admin gửi lên về đĩa (file tạm) — chỗ DUY NHẤT trong repo chạm vào
API tải file của Telegram.

Trả về `UploadedFile` mang ĐƯỜNG DẪN, không phải bytes — xem
TAR_agent/graph_admin/state.py để biết vì sao. `UploadedFile` khai ở đó, tầng này chỉ đi
điền.

File tạm KHÔNG mang đuôi thật (`uuid4().hex` trơn, không `.xlsx`/`.txt`): giữ
đúng điều kiện thật của Telegram (`bot.download` không tự thêm đuôi), để
loader nào giả định sai về đuôi file sẽ lộ ra ngay khi kiểm chứ không được
che giấu bởi cách mình đặt tên file tạm.
"""

import logging
import tempfile
import time
import uuid
from pathlib import Path

from aiogram import Bot
from aiogram.types import Document, Message

from TAR_agent.graph_admin.state import UploadedFile
from TAR_agent.graph_admin.helpers.loaders import SUPPORTED
from TAR_agent.utils.config import settings

log = logging.getLogger(__name__)

_UPLOAD_DIR = Path(tempfile.gettempdir()) / "tar_uploads"


class UploadRejected(Exception):
    """File không nhận: quá nặng, sai định dạng, hoặc không tải được.

    `reason` là MÃ, giống quy ước `error` trong graph_admin — render.py mới
    dịch sang tiếng Việt, chỗ này không viết văn cho người đọc.
    """

    def __init__(self, reason: str, **data):
        self.reason = reason
        self.data = data
        super().__init__(reason)


def _accepted_name(document: Document | None) -> str:
    """Tên file nếu nhận, ngược lại ném UploadRejected.

    Tách khỏi `download_document` để phần kiểm và phần tải không lẫn vào nhau:
    mọi lý do TỪ CHỐI nằm gọn ở đây, đọc một lượt là đủ.

    Kiểm đuôi TRƯỚC kích thước: file .zip 500MB thì lý do đáng nói là định dạng,
    không phải cân nặng.
    """
    if document is None:
        raise UploadRejected("no_document")

    file_name = document.file_name or f"file_{document.file_unique_id}"
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED:
        raise UploadRejected("unsupported_format", file_name=file_name, suffix=suffix)
    if (document.file_size or 0) > settings.max_upload_mb * 1024 * 1024:
        raise UploadRejected(
            "too_large", file_name=file_name, max_mb=settings.max_upload_mb
        )
    return file_name


async def download_document(bot: Bot, message: Message) -> UploadedFile:
    document = message.document
    file_name = _accepted_name(document)

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPLOAD_DIR / uuid.uuid4().hex
    try:
        await bot.download(document, destination=dest)
    except Exception as exc:
        log.exception("Tải file thất bại: %s", file_name)
        raise UploadRejected("download_failed", file_name=file_name) from exc

    return UploadedFile(file_name=file_name, path=dest, size_bytes=dest.stat().st_size)


def sweep_stale_uploads(max_age_hours: int = 24) -> int:
    """Dọn file tạm sót lại sau khi app restart giữa lúc admin chưa bấm xong.

    `report` đã xoá file tạm ở mọi đường ra bình thường của một lượt chạy; hàm
    này chỉ vét phần KHÔNG đi qua report — thread bị bỏ dở vì app tắt giữa hai
    điểm dừng (chọn dự án / xác nhận ghi đè). Gọi lúc khởi động, không phải
    theo lịch: dở dang chỉ có thể xảy ra quanh lúc restart.
    """
    if not _UPLOAD_DIR.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in _UPLOAD_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            log.warning("Không xoá được file tạm cũ %s", path, exc_info=True)
    return removed
