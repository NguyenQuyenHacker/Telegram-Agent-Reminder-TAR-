"""Chuẩn hoá nội dung đầu việc.

DB lưu nguyên văn dòng báo cáo (rule 4 của prompt trích) để còn đối chiếu lại
được. Nhưng có hai chỗ cần bản đã gọt thay vì bản nguyên văn:

  - tính task_id (R7): hạn KHÔNG được là một phần căn cước công việc, nếu không
    thì sửa hạn rồi gửi lại báo cáo sẽ đẻ ra một dòng mới thay vì cập nhật;
  - hiển thị tin nhắn nhắc: mức ưu tiên và hạn đã có dòng riêng nói hộ.

Hai chỗ đó phải gọt GIỐNG HỆT nhau, nên dùng chung một hàm ở đây.
"""

import re

# "(hạn 20/8)", "(Hạn: 20/8)", "(hết hạn 20/8)" — mọi biến thể trong ngoặc đơn
_INLINE_DUE = re.compile(r"\s*\(\s*(?:hết\s+)?hạn\b[^)]*\)", re.IGNORECASE)

_URGENT_PREFIX = "ƯU TIÊN:"


def normalize_content(content: str) -> str:
    """Bỏ tiền tố ưu tiên và phần '(hạn ...)' khỏi nội dung đầu việc."""
    stripped = _INLINE_DUE.sub("", content.removeprefix(_URGENT_PREFIX))
    return stripped.strip(" -–—:")
