"""Nạp prompt từ file .md, ưu tiên bản trên Langfuse.

Vì sao tách khỏi .py: prompt là thứ sửa nhiều nhất khi tinh chỉnh agent. Để
nguyên trong code thì mỗi lần đổi một câu là phải sửa code, commit, deploy lại.
Tách ra .md rồi đẩy lên Langfuse (label "production") thì sửa và rollback ngay
trên UI, code không đụng tới.

Thứ tự lấy prompt:
  1. Langfuse label "production" — nếu đã cấu hình LANGFUSE_PUBLIC_KEY/SECRET_KEY
  2. File .md cạnh module này — luôn là fallback

Bước 2 không chỉ để dự phòng: get_prompt(fallback=...) của Langfuse KHÔNG raise
khi mất mạng hay sai key, nó lặng lẽ dùng fallback. Nhờ vậy Langfuse chết không
kéo agent chết theo, và ai clone repo về chạy mà không có tài khoản Langfuse
vẫn dùng được ngay.

Biến trong prompt viết theo cú pháp Langfuse `{{TÊN_BIẾN}}` (không phải
str.format), để cùng một file .md dùng được cho cả hai đường lấy prompt.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_PROMPT_DIR = Path(__file__).parent
# Tên trên Langfuse: reminder-agent/<name>, gom prompt của dự án vào một chỗ
_LANGFUSE_NAMESPACE = "reminder-agent"


@lru_cache(maxsize=None)
def _read_local(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy prompt: {path}")
    return path.read_text(encoding="utf-8").strip()


def _langfuse_enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _compile_local(template: str, variables: dict[str, str]) -> str:
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def load_prompt(name: str, **variables: str) -> str:
    """Trả về nội dung prompt đã thay biến.

    Args:
        name: tên file .md (không kèm đuôi), ví dụ "extract_system".
        **variables: biến thay vào `{{TÊN}}`, ví dụ TODAY="2026-08-10".
    """
    local = _read_local(name)

    if not _langfuse_enabled():
        return _compile_local(local, variables)

    try:
        from langfuse import get_client

        prompt = get_client().get_prompt(
            f"{_LANGFUSE_NAMESPACE}/{name}",
            label="production",
            fallback=local,
        )
        return prompt.compile(**variables)
    except ImportError:
        # Có key nhưng chưa cài gói -> đừng làm sập agent vì chuyện quan sát
        log.warning("Đã đặt LANGFUSE_* nhưng chưa cài gói langfuse, dùng prompt .md")
        return _compile_local(local, variables)
