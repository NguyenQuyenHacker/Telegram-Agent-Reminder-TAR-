"""Lấy ảnh / tin nhắn thoại ra khỏi một update Telegram và tải về bytes.

Chỉ có I/O Telegram ở đây: đọc xem update mang gì, và tải file về. Việc hiểu nội
dung là của reminder_agent/utils/media_text.py — tách ra để đổi model hay đổi
prompt không phải đụng vào tầng mạng, và ngược lại.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from aiogram.types import Message

from app.telegram.bot import bot

log = logging.getLogger(__name__)

MediaKind = Literal["photo", "voice"]

# Telegram Bot API không cho bot tải file quá 20MB, nhưng vẫn phải tự chặn:
# getFile trả về kích thước trước khi tải, còn để nó chạy tới lúc tải mới hỏng
# thì người dùng chờ hết timeout mới nhận được lời từ chối.
_MAX_MEDIA_BYTES = 20 * 1024 * 1024

# Telegram không gắn mime cho ảnh (nó luôn nén về JPEG), còn voice thì có gắn
# nhưng vẫn có update thiếu — mặc định theo đúng định dạng Telegram dùng.
_PHOTO_MIME = "image/jpeg"
_VOICE_MIME = "audio/ogg"
_AUDIO_MIME = "audio/mpeg"


@dataclass(frozen=True)
class IncomingMedia:
    kind: MediaKind
    file_id: str
    mime_type: str
    # Chú thích người dùng gõ kèm ảnh. Đi cùng ảnh vào model chứ không xử lý
    # riêng: "cái này hạn thứ 6 nhé" chỉ có nghĩa khi đọc cùng tấm ảnh.
    caption: str


def extract_media(message: Message) -> IncomingMedia | None:
    """Update này mang ảnh hay tiếng nói? Không thì None.

    Ảnh gửi dưới dạng file (document) cũng nhận: người dùng gửi ảnh chụp màn
    hình bằng nút "gửi không nén" là chuyện thường, mà nội dung thì y hệt.
    """
    caption = message.caption or ""

    if message.photo:
        # photo là danh sách nhiều cỡ của CÙNG một ảnh, cỡ cuối là nét nhất —
        # chữ trong ảnh chụp màn hình sống hay chết là ở đây.
        return IncomingMedia("photo", message.photo[-1].file_id, _PHOTO_MIME, caption)

    if message.voice:
        mime = message.voice.mime_type or _VOICE_MIME
        return IncomingMedia("voice", message.voice.file_id, mime, caption)

    if message.audio:
        mime = message.audio.mime_type or _AUDIO_MIME
        return IncomingMedia("voice", message.audio.file_id, mime, caption)

    document = message.document
    if document and document.mime_type:
        if document.mime_type.startswith("image/"):
            return IncomingMedia("photo", document.file_id, document.mime_type, caption)
        if document.mime_type.startswith("audio/"):
            return IncomingMedia("voice", document.file_id, document.mime_type, caption)

    return None


async def download_media(media: IncomingMedia) -> bytes | None:
    """Tải file về bộ nhớ. None nếu quá lớn hoặc Telegram không trả file.

    Giữ trong RAM chứ không ghi ra đĩa: file lớn nhất được nhận là 20MB, và ghi
    ra đĩa thì phải có ai đó đi dọn.
    """
    file = await bot.get_file(media.file_id)
    if file.file_size and file.file_size > _MAX_MEDIA_BYTES:
        log.warning("MEDIA: file %d byte, quá lớn", file.file_size)
        return None
    if not file.file_path:
        log.warning("MEDIA: Telegram không trả file_path cho %s", media.file_id)
        return None

    buffer = await bot.download_file(file.file_path)
    if buffer is None:
        return None
    return buffer.read()
