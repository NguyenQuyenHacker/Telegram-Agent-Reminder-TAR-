"""Chuẩn hoá nội dung đầu việc.

Bản đã gọt là thứ ĐƯỢC LƯU vào DB, không phải nguyên văn dòng báo cáo. Giữ
nguyên văn thì hạn nằm ở hai nơi — cột `due_date` và cái đuôi "(hạn 20/8)" trong
`content` — mà lệnh dời hạn chỉ chạm được một; đổi hạn xong là hai chỗ đá nhau,
và tool query_tasks đưa cả hai cho LLM đọc.

Cùng hàm này còn dùng để tính task_id (R7): hạn KHÔNG được là một phần căn cước
công việc, nếu không thì sửa hạn rồi gửi lại báo cáo sẽ đẻ ra một dòng mới thay
vì cập nhật.
"""

import re

# "(hạn 20/8)", "(Hạn: 20/8)", "(hết hạn 20/8)" — mọi biến thể trong ngoặc đơn.
# Ngoặc đóng là TUỲ CHỌN: báo cáo gõ thiếu ")" là chuyện thường, mà bỏ sót một
# lần thì cái đuôi hạn chui vào task_id, sau này gõ đúng lại đẻ ra dòng khác.
_INLINE_DUE = re.compile(r"\s*\(\s*(?:hết\s+)?hạn\b[^)]*(?:\)|$)", re.IGNORECASE)

_URGENT_PREFIX = "ƯU TIÊN:"


def normalize_content(content: str) -> str:
    """Bỏ tiền tố ưu tiên và phần '(hạn ...)' khỏi nội dung đầu việc."""
    stripped = _INLINE_DUE.sub("", content.removeprefix(_URGENT_PREFIX))
    return stripped.strip(" -–—:")
