"""Event của lõi -> chuỗi gửi lên Telegram.

Chỗ DUY NHẤT biết cả hai thứ: hình dạng event của lõi, và cách Telegram hiển
thị. Lõi không import module này, module này không được gọi ngược vào lõi.

Kiểu `Event` khai lại ở đây thay vì import từ graph_admin.state hay
graph_client.state: đây là điểm hợp của cả hai luồng, buộc nó phụ thuộc một
graph cụ thể là buộc nhầm chiều. Hai bên chỉ cần thoả `{"kind": ..., "data": ...}`.

Phần lớn event chỉ là một câu có chỗ trống -> khai bằng CHUỖI MẪU trong `_TEXT`,
thêm một kind mới là thêm một dòng. Chỉ kind nào thật sự cần rẽ nhánh (đếm đoạn,
liệt kê dự án, tra mã lỗi) mới có hàm riêng trong `_HANDLER`.

Điền chỗ trống bằng `format_map` với dict trả "" cho khoá thiếu: lõi đổi hình
dạng `data` mà quên sửa đây thì admin nhận câu thiếu một mẩu, chứ không phải
KeyError rồi im lặng.

Gửi TEXT THUẦN (sender.py mặc định parse_mode=None) — nên không cần escape gì
cả, kể cả tên file hay nội dung do người dùng gõ. Đổi sang HTML thì phải tự lo
escape ở đây trước.
"""

import logging
from collections.abc import Callable
from typing import Any

from typing_extensions import TypedDict

log = logging.getLogger(__name__)


class Event(TypedDict):
    kind: str
    data: dict[str, Any]


class _Blank(dict):
    """Khoá thiếu -> chuỗi rỗng, không ném KeyError."""

    def __missing__(self, key: str) -> str:
        log.warning("Thiếu khoá %r trong data của event", key)
        return ""


def _fill(template: str, data: dict[str, Any]) -> str:
    return template.format_map(_Blank(data))


# Câu thuần, chỉ điền chỗ trống. kind mới mà không cần rẽ nhánh thì thêm ở đây.
_TEXT = {
    "ingest_unchanged": (
        "{file_name} · {project_name}: nội dung y hệt bản đang có "
        "({chunk_count} đoạn) — không nạp lại."
    ),
    "ingest_cancelled": "Đã huỷ. {file_name} · {project_name} giữ nguyên bản cũ.",
    "hint": (
        "Gửi file .txt hoặc .xlsx để nạp vào kho, "
        "hoặc gõ /duan <tên> để tạo/xem dự án."
    ),
    "project_created": '✓ Đã tạo dự án "{name}"',
    "project_exists": 'Dự án "{name}" đã có sẵn.',
    "project_invalid_name": (
        "Tên dự án không hợp lệ (toàn ký tự đặc biệt/emoji). Gõ /duan <tên khác>."
    ),
    # Hỏng ở TẦNG NGOÀI graph: mất kết nối DB, checkpoint không đọc được... Không
    # nêu chi tiết kỹ thuật — admin không sửa được `SSL connection has been
    # closed`, chi tiết nằm ở log server. Điều DUY NHẤT admin cần biết: yêu cầu
    # đó KHÔNG chạy, và thử lại được.
    "system_error": "Hệ thống đang gặp trục trặc, chưa xử lý được. Thử gửi lại sau ít phút.",
}

# reason -> câu, dùng chung cho ingest_failed (lỗi trong graph) và upload_rejected
# (lỗi trước khi vào graph). Mã lạ rơi về câu chung CÓ KÈM MÃ để còn tra log.
_FAIL_TEXT = {
    "no_projects": "Kho chưa có dự án nào. Gõ /duan <tên dự án> trước rồi gửi lại file.",
    "project_gone": "Dự án vừa chọn không còn nữa. Gửi lại file để chọn dự án khác.",
    "temp_file_gone": "File tạm đã mất (bot có thể vừa khởi động lại). Gửi lại file giúp mình.",
    "parse_failed": "Không đọc được nội dung file này. Kiểm tra lại rồi gửi lại.",
    "empty_document": "File không có nội dung đọc được (trống, hoặc chỉ có ảnh scan).",
    "embed_failed": "Lỗi khi tạo vector — thử gửi lại file sau ít phút.",
    "write_failed": "Lỗi khi ghi vào kho — thử gửi lại file sau ít phút.",
    "unsupported_format": "Chưa hỗ trợ định dạng {suffix}. Gửi .txt hoặc .xlsx.",
    "too_large": "File {file_name} quá nặng (giới hạn {max_mb}MB).",
    "download_failed": "Tải {file_name} thất bại, thử gửi lại.",
    "no_document": "Không nhận được file nào.",
}


def _ingest_written(verb: str) -> Callable[[dict[str, Any]], str]:
    """Dựng hàm render cho ingest_done / ingest_updated — khác mỗi động từ."""

    def render_written(data: dict[str, Any]) -> str:
        lines = [_fill(f"✓ {verb} {{file_name}} · {{project_name}}", data)]
        replaced = data.get("replaced_chunk_count")
        lines.append(
            f"{data.get('chunk_count', 0)} đoạn"
            + (f" (thay {replaced} đoạn cũ)" if replaced else "")
        )
        if data.get("as_of_date"):
            lines.append(f"Mốc dữ liệu: {data['as_of_date']}")
        return "\n".join(lines)

    return render_written


def _failed(data: dict[str, Any]) -> str:
    reason = data.get("reason", "")
    template = _FAIL_TEXT.get(reason)
    if template is None:
        return f"Có lỗi khi xử lý file (mã lỗi: {reason})."
    return _fill(template, data)


def _project_list(data: dict[str, Any]) -> str:
    projects = data.get("projects") or []
    if not projects:
        return "Kho chưa có dự án nào. Gõ /duan <tên dự án> để tạo."
    lines = ["Danh sách dự án:"]
    lines += [f"• {p['name']} ({p['document_count']} tài liệu)" for p in projects]
    return "\n".join(lines)


# Chỉ kind cần rẽ nhánh mới nằm ở đây; còn lại là chuỗi mẫu trong _TEXT.
_HANDLER: dict[str, Callable[[dict[str, Any]], str]] = {
    "ingest_done": _ingest_written("Đã nạp"),
    "ingest_updated": _ingest_written("Đã cập nhật"),
    "ingest_failed": _failed,
    "upload_rejected": _failed,
    "project_list": _project_list,
}


def render(event: Event) -> str:
    """kind lạ hay data sai hình dạng đều rơi về một câu chung — một event không
    render được không đáng làm chết cả lượt trả lời."""
    kind = event.get("kind", "")
    data = event.get("data") or {}
    try:
        handler = _HANDLER.get(kind)
        if handler is not None:
            return handler(data)
        if kind in _TEXT:
            return _fill(_TEXT[kind], data)
    except Exception:
        log.exception("Render hỏng cho event %r", event)
        return "Đã xử lý xong (không hiển thị được chi tiết)."

    log.warning("Không có renderer cho kind=%r", kind)
    return "Đã xử lý xong."
