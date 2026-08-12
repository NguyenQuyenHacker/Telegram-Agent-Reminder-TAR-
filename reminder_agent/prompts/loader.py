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

# Prompt chưa từng được đẩy lên Langfuse. Hỏi Langfuse về một tên không tồn tại
# tốn 1-3 GIÂY mỗi lần: nó trả fallback chứ không raise, nhưng lượt sau lại đi
# hỏi lại y hệt (nó xoá tên hỏng khỏi cache thay vì nhớ là hỏng). Hai prompt mỗi
# lượt chat là mất vài giây trước khi kịp gọi LLM. Nhớ tên hỏng ở đây để chỉ trả
# giá đúng một lần cho mỗi tiến trình.
_missing_on_langfuse: set[str] = set()


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

    if not _langfuse_enabled() or name in _missing_on_langfuse:
        return _compile_local(local, variables)

    try:
        from langfuse import get_client

        prompt = get_client().get_prompt(
            f"{_LANGFUSE_NAMESPACE}/{name}",
            label="production",
            fallback=local,
        )
    except ImportError as exc:
        # Có key nhưng chưa cài gói -> đừng làm sập agent vì chuyện quan sát
        log.warning("Dùng prompt .md, không gọi được Langfuse: %s", exc)
        return _compile_local(local, variables)

    # is_fallback = Langfuse không trả được prompt này (chưa đẩy lên, hoặc mạng
    # hỏng). Ghi tên lại để khỏi trả giá chờ mạng ở mọi lượt chat sau.
    if getattr(prompt, "is_fallback", False):
        _missing_on_langfuse.add(name)
        log.warning(
            "Prompt '%s' không có trên Langfuse, dùng bản .md và thôi không hỏi lại. "
            "Đẩy nó lên rồi khởi động lại nếu muốn sửa prompt trên UI.",
            name,
        )
        return _compile_local(local, variables)

    return prompt.compile(**variables)


def warm_up() -> None:
    """Nạp trước mọi prompt lúc khởi động.

    Lượt hỏi Langfuse đầu tiên của mỗi prompt tốn vài giây dù thành công hay
    không. Trả giá đó ở đây, lúc chưa ai chờ, thay vì để rơi vào tin nhắn đầu
    tiên của người dùng.
    """
    if not _langfuse_enabled():
        return
    for path in sorted(_PROMPT_DIR.glob("*.md")):
        load_prompt(path.stem)
