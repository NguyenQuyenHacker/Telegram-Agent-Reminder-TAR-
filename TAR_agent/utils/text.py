"""Chuẩn hoá chuỗi tiếng Việt và sinh định danh từ tên.

Module thuần: không I/O, không đọc cấu hình, không phụ thuộc tầng nào khác.

Ba việc chính:
  - `name_uuid()` sinh `project_id` ổn định từ tên dự án. Nhờ nó, "App Trưởng
    thôn" trong file xlsx và "app trưởng thôn" trong biên bản PDF rơi về ĐÚNG
    một dự án mà không cần tra DB.
  - `document_uuid()` / `chunk_uuid()` sinh khoá chính của tài liệu và chunk,
    cũng suy từ danh tính chứ không random.
  - `ilike_pattern()` vô hiệu ký tự đại diện của LIKE trước khi ghép mẫu tìm kiếm.
"""

import re
import unicodedata
import uuid

_WORD_RE = re.compile(r"[a-z0-9]+")

# Namespace cố định của dự án. Đổi hằng số này là mọi project_id đã lưu trỏ vào
# hư không, nên nó phải nằm im mãi mãi.
_PROJECT_NAMESPACE = uuid.UUID("0a5c15f7-531f-57b1-9812-79ea011f54da")

# Namespace của tài liệu, suy ra từ namespace dự án nên không cần thêm một hằng
# số ma nữa — và cũng bất biến y như nó.
_DOCUMENT_NAMESPACE = uuid.uuid5(_PROJECT_NAMESPACE, "source_document")

# Số thứ tự đầu dòng ("2/ App Trưởng thôn") không thuộc về tên dự án.
_LEADING_ORDINAL = re.compile(r"^\s*\d+\s*[/.)\-]\s*")

# Ký tự escape cho ILIKE. Chuỗi người dùng gõ đi thẳng vào mẫu LIKE, không vô
# hiệu '%' và '_' thì gõ "100%" là quét sạch bảng.
_LIKE_ESCAPE = "\\"


def ascii_lower(text: str) -> str:
    """Bỏ dấu tiếng Việt. "Đ" phải thay tay: NFD không tách được nó ra d + gạch."""
    plain = text.replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", plain)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalize_name(name: str) -> str:
    """Khoá định danh của một cái tên: bỏ số thứ tự, dấu, emoji, dấu câu.

    Mọi biến thể cách viết của cùng một dự án phải rơi về đúng chuỗi này.
    """
    return " ".join(_WORD_RE.findall(ascii_lower(_LEADING_ORDINAL.sub("", name))))


def name_uuid(name: str) -> uuid.UUID:
    """uuid5(namespace, tên đã chuẩn hoá). Thuần, không tra DB."""
    return uuid.uuid5(_PROJECT_NAMESPACE, normalize_name(name))


def document_uuid(project_id: uuid.UUID, file_name: str) -> uuid.UUID:
    """Khoá chính của một tài liệu — suy từ (dự án, TÊN FILE), không phải nội dung.

    Đây là thứ khiến "nạp lại cùng tên" thành ghi đè: cùng tên thì cùng
    document_id, nên bản mới thay chỗ bản cũ và mọi tham chiếu tới tài liệu vẫn
    đúng sau khi nội dung đổi.

    Suy từ nội dung thì ngược lại: sửa một chữ trong file là ra một id khác, và
    kho có hai bản của cùng một tài liệu.
    """
    return uuid.uuid5(_DOCUMENT_NAMESPACE, f"{project_id}:{file_name}")


def chunk_uuid(document_id: uuid.UUID, chunk_index: int) -> uuid.UUID:
    """Khoá chính của một chunk. Dùng chính document_id làm namespace.

    Nạp lại cùng một tài liệu thì chunk số 5 vẫn mang đúng id cũ — tiện khi soi
    log hoặc so hai lần nạp, và không cần thêm một hằng số namespace nữa.
    """
    return uuid.uuid5(document_id, str(chunk_index))


def ilike_pattern(text: str) -> str:
    """Chuỗi người dùng gõ -> mẫu khớp lỏng, đã vô hiệu ký tự đại diện của LIKE.

    Nơi gọi phải truyền kèm `escape=LIKE_ESCAPE` cho `ilike()`.
    """
    escaped = (
        text.strip()
        .replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


LIKE_ESCAPE = _LIKE_ESCAPE
