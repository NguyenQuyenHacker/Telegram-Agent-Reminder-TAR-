"""Mã việc người đọc được: "TB-002".

`task_id` (băm sha256 của nhóm + nội dung) là căn cước idempotent của R7 —
không ai gõ được nó vào Telegram. Mã ở đây là thứ người dùng nhắc tới trong câu
"TB-002 xong rồi", nên phải ngắn, gõ nhanh, và ổn định.

Module thuần: không I/O, không hằng số nghiệp vụ, không đọc cấu hình.
"""

import re
import string
import unicodedata
import uuid
from collections.abc import Iterator

_WORD_RE = re.compile(r"[a-z0-9]+")

# Namespace cố định của dự án. Đổi hằng số này là mọi group_id đã lưu trỏ vào
# hư không, nên nó phải nằm im mãi mãi.
_GROUP_NAMESPACE = uuid.UUID("0a5c15f7-531f-57b1-9812-79ea011f54da")

# Số thứ tự đầu dòng của báo cáo ("2/ App Trưởng thôn") không thuộc về tên nhóm.
# Prompt có dặn LLM gọt, nhưng một lượt nó lười là nhóm bị tách đôi, nên gọt lại
# ở đây cho chắc.
_LEADING_ORDINAL = re.compile(r"^\s*\d+\s*[/.)\-]\s*")

# Chấp nhận "TB-002", "TB002", "tb2", "#TB-2". Hai lookaround chặn việc bắt
# nhầm một mẩu giữa chữ khác ("abc123" không phải mã việc).
_CODE_RE = re.compile(
    r"(?<![0-9A-Za-z])#?([A-Za-z]{2,4})-?([0-9]{1,4})(?![0-9A-Za-z])"
)

_FALLBACK_PREFIX = "XX"
_PREFIX_LENGTH = 2
# Tiền tố gốc đã bị chiếm thì thử bản dài hơn trước, rồi mới tới ghép chữ cái
_LONGER_PREFIX_LENGTHS = (3, 4)
# Chữ độn khi tên nhóm ngắn hơn _PREFIX_LENGTH ("A" -> "AX")
_PREFIX_PADDING = "X"
_SEQ_DIGITS = 3


def _ascii_lower(text: str) -> str:
    """Bỏ dấu tiếng Việt. "Đ" phải thay tay: NFD không tách được nó ra d + gạch."""
    plain = text.replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", plain)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _initials(group: str) -> str:
    """Chữ cái đầu của mọi từ trong tên nhóm.

    Cố ý KHÔNG có danh sách từ đệm phải bảo trì. Lấy hết rồi giữ hai chữ cuối
    cũng ra đúng phần mang nghĩa, vì tên nhóm tiếng Việt đặt bổ ngữ ở cuối:
    "App Trưởng thôn, trưởng bản" -> atttb -> TB.
    """
    return "".join(word[0] for word in _WORD_RE.findall(_ascii_lower(group)))


def normalize_group(group: str) -> str:
    """Khoá định danh của một nhóm: bỏ số thứ tự, dấu tiếng Việt, emoji, dấu câu.

    Mọi biến thể cách viết của cùng một nhóm phải rơi về đúng chuỗi này. Trước
    đây task_id băm bản `lower()` còn bộ đếm mã tra bản nguyên văn, nên "App
    Trưởng thôn" và "app trưởng thôn" ra cùng task_id mà lại đẻ hai bộ đếm.
    """
    return " ".join(_WORD_RE.findall(_ascii_lower(_LEADING_ORDINAL.sub("", group))))


def group_uuid(group: str) -> uuid.UUID:
    """group_id = uuid5(namespace, tên đã chuẩn hoá). Thuần, không tra DB."""
    return uuid.uuid5(_GROUP_NAMESPACE, normalize_group(group))


def derive_prefix(group: str) -> str:
    """Tiền tố hai chữ cái của một nhóm việc.

    Một từ thì lấy hai chữ đầu của chính từ đó ("Kho" -> KH), nhiều từ thì lấy
    hai chữ cái đầu cuối cùng ("Hệ thống báo cáo" -> BC).
    """
    words = _WORD_RE.findall(_ascii_lower(group))
    if not words:
        return _FALLBACK_PREFIX
    if len(words) == 1:
        first_word = words[0][:_PREFIX_LENGTH].upper()
        return first_word.ljust(_PREFIX_LENGTH, _PREFIX_PADDING)
    return _initials(group)[-_PREFIX_LENGTH:].upper()


def prefix_alternatives(group: str) -> Iterator[str]:
    """Phương án thay thế khi tiền tố đã bị nhóm khác chiếm, theo thứ tự ưu tiên.

    Chỉ dùng chữ cái, KHÔNG thêm hậu tố số: "TB2-001" mà người dùng gõ tắt
    "tb2" thì không phân biệt được với "TB-002".
    """
    initials = _initials(group)
    seen = {derive_prefix(group)}
    for length in _LONGER_PREFIX_LENGTHS:
        if len(initials) >= length:
            candidate = initials[-length:].upper()
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
    base = derive_prefix(group)
    for letter in string.ascii_uppercase:
        candidate = base + letter
        if candidate not in seen:
            yield candidate


def format_code(prefix: str, seq: int) -> str:
    return f"{prefix}-{seq:0{_SEQ_DIGITS}d}"


def parse_code(text: str) -> str | None:
    """Chuẩn hoá cách gõ tắt của người dùng về mã đầy đủ, hoặc None nếu không phải mã."""
    match = _CODE_RE.search(text.strip())
    if match is None:
        return None
    return format_code(match.group(1).upper(), int(match.group(2)))
