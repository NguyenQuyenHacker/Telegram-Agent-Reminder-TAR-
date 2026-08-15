"""Event của lõi -> chuỗi gửi lên Telegram.

Chỗ DUY NHẤT biết cả hai thứ: hình dạng event của lõi, và cách Telegram hiển
thị. Lõi không import module này, module này không được gọi ngược vào lõi.

Kiểu `Event` khai lại ở đây thay vì import từ graph_admin.state hay
graph_client.state: đây là điểm hợp của cả hai luồng, buộc nó phụ thuộc một
graph cụ thể là buộc nhầm chiều. Hai bên chỉ cần thoả `{"kind": ..., "data": ...}`.

Phần lớn event chỉ là một câu có chỗ trống -> khai bằng CHUỖI MẪU trong `_TEXT`,
thêm một kind mới là thêm một dòng. Chỉ kind nào thật sự cần rẽ nhánh (liệt kê
dự án, tra mã lỗi, dựng câu trả lời client) mới có hàm riêng trong `_HANDLER`.

Điền chỗ trống bằng `format_map` với dict trả "" cho khoá thiếu: lõi đổi hình
dạng `data` mà quên sửa đây thì admin nhận câu thiếu một mẩu, chứ không phải
KeyError rồi im lặng.

Gửi TEXT THUẦN (sender.py mặc định parse_mode=None) — nên không cần escape gì
cả, kể cả tên file hay nội dung do người dùng gõ.

NGOẠI LỆ DUY NHẤT: kind `answer` (câu trả lời của bot client) gửi bằng HTML, xem
`_HTML_KINDS` và `parse_mode_of`. Câu trả lời là thứ người dùng đọc hàng chục lần
một ngày nên nó đáng có tiêu đề đậm và dòng nguồn nhạt; các event còn lại là
thông báo cho admin, chữ thuần là đủ. Mọi phần chữ động đi vào nhánh đó PHẢI qua
`_esc` — chỉ cần một dấu '<' của người dùng lọt ra là Telegram trả 400 và tin
nhắn mất luôn.
"""

import html
import logging
import re
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
    # Một câu, không kèm số đoạn / số dòng / ánh xạ cột. Những con số đó là số
    # liệu chẩn đoán: chúng vẫn nằm trong `data` của event và trong log server,
    # nhưng admin đọc trên Telegram chỉ cần biết file đã vào đúng dự án.
    "ingest_done": '✓ Đã nạp file "{file_name}" vào dự án "{project_name}" thành công',
    "ingest_updated": (
        '✓ Đã cập nhật file "{file_name}" trong dự án "{project_name}" thành công'
    ),
    "hint": (
        "Gửi file .txt hoặc .xlsx để nạp vào kho, "
        "gõ /duan <tên> để tạo/xem dự án, "
        "hoặc /reindex <tên dự án> để nạp lại các file cũ."
    ),
    "reindex_usage": "Gõ /reindex <tên dự án> — cần tên dự án để biết gỡ cho cái nào.",
    "reindex_done": (
        "Đã gỡ dấu vân tay của {document_count} tài liệu trong \"{name}\". "
        "Gửi lại các file đó để chúng được trích dữ liệu bảng."
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

# Telegram chặn ở 4096 ký tự. Chừa chỗ cho câu báo đã cắt.
_MAX_ANSWER_CHARS = 4000

# reason -> câu, cho luồng client. Tách khỏi _FAIL_TEXT vì hai luồng nói với hai
# người khác nhau: admin sửa được (gửi lại file), người hỏi thì không — với họ
# điều duy nhất dùng được là "hỏi lại đi".
_CLIENT_FAIL_TEXT = {
    "llm_failed": "Mình đang không gọi được mô hình để trả lời. Hỏi lại giúp mình sau ít phút.",
    "empty_answer": "Mình chưa soạn được câu trả lời cho câu này. Bạn thử hỏi cụ thể hơn xem.",
}


def _failed(data: dict[str, Any]) -> str:
    reason = data.get("reason", "")
    template = _FAIL_TEXT.get(reason)
    if template is None:
        return f"Có lỗi khi xử lý file (mã lỗi: {reason})."
    return _fill(template, data)


# --- dựng câu trả lời client ---------------------------------------------
#
# compose sinh ra một khối chữ liền: tên dự án mở đầu MỌI câu trả lời, dẫn nguồn
# `[file · ngày]` bám đuôi TỪNG ý. Đọc trên điện thoại thì ba thứ lặp lại đó dài
# hơn cả nội dung. Ở đây tách chúng ra ba vùng:
#
#     <b>📁 Dự án</b>        tiêu đề, dựng từ project_name của state
#     nội dung              đã gỡ tiền tố và dẫn nguồn, gạch đầu dòng thành "•"
#     <i>📄 nguồn</i>       gộp về MỘT dòng cuối
#
# Tên dự án ở tiêu đề lấy từ state chứ không phải từ chữ model viết ra — đúng
# điều mà luật "mở đầu bằng tên dự án" trong compose.md muốn bảo đảm, nhưng
# không phụ thuộc việc model có nhớ làm hay không.

# Dẫn nguồn: dấu "·" là thứ phân biệt nó với ngoặc vuông thường trong câu.
_CITATION = re.compile(r"[ \t]*\[([^\[\]]*·[^\[\]]*)\]")

# "2026-08-13" -> "13/08/2026". Người Việt đọc ngày trước.
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Gạch đầu dòng model hay gõ, quy về một ký hiệu.
_BULLET = re.compile(r"^[ \t]*[-*·•][ \t]+")

# 3 dòng trống trở lên -> 1 dòng trống.
_BLANK_RUN = re.compile(r"\n{3,}")

# Markdown model vẫn gõ dù compose.md cấm — prompt là lời dặn, không phải ràng
# buộc. Câu trả lời gửi bằng HTML nên `**` không thành chữ đậm, nó nằm nguyên
# trên màn hình người đọc, và đó là thứ duy nhất KHÔNG sửa được bằng cách dặn
# kỹ hơn.
#
# Ba biểu thức này chạy SAU `_esc`: `html.escape` không tạo ra cũng không xoá đi
# dấu `*` hay `#` nào, nên khớp trên chuỗi đã escape vẫn đúng — mà thẻ `<b>`
# chèn vào thì không bị escape theo.
_MD_BOLD = re.compile(r"\*\*(?=\S)([^\n]+?)\*\*")
_MD_ITALIC = re.compile(r"\*(?=\S)([^\n*]+?)\*")
_MD_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
# Dấu sao lẻ còn sót = markdown hụt một vế. Gỡ luôn: một tài liệu tiến độ xây
# dựng gần như không bao giờ có dấu sao thật, mà để lại thì nó hiện ra màn hình.
_MD_STRAY = re.compile(r"\*+")

_CLIPPED = "\n\n[…] Câu trả lời quá dài, đã cắt bớt."


def _esc(text: str) -> str:
    """Chữ động -> an toàn trong HTML của Telegram.

    `quote=False`: Telegram không cần escape dấu nháy, mà `&quot;` hiện ra
    trong tên file thì xấu.
    """
    return html.escape(text, quote=False)


def _markdown(escaped: str) -> str:
    """Markdown sót lại trong câu trả lời -> HTML của Telegram.

    Nhận chuỗi ĐÃ escape và trả chuỗi có thẻ — gọi nó trên chữ chưa escape thì
    `<b>` vừa chèn sẽ bị `_esc` biến thành `&lt;b&gt;` ngay sau đó.
    """
    text = _MD_HEADING.sub("", escaped)
    text = _MD_BOLD.sub(r"<b>\1</b>", text)
    text = _MD_ITALIC.sub(r"<i>\1</i>", text)
    return _MD_STRAY.sub("", text)


def _pretty_date(text: str) -> str:
    match = _ISO_DATE.match(text)
    return f"{match[3]}/{match[2]}/{match[1]}" if match else text


def _source_label(inside: str) -> str:
    """Ruột một dẫn nguồn -> nhãn hiển thị `file · ngày`.

    Phần thứ ba (heading_path) bị bỏ: nó là đường dẫn tiêu đề trong file, hữu
    ích cho model lúc tra chứ không nói gì với người đọc.
    """
    parts = [part.strip() for part in inside.split("·")]
    file_name = parts[0]
    date = _pretty_date(parts[1]) if len(parts) > 1 else ""
    return f"{file_name} · {date}" if date else file_name


def _pull_sources(text: str) -> tuple[str, list[str]]:
    """Gỡ mọi dẫn nguồn khỏi thân bài, trả về (thân bài, danh sách nguồn).

    Một nguồn (gần như mọi lượt, vì kho mỗi dự án thường một file) thì gỡ sạch,
    dòng cuối nói đủ. Từ hai nguồn trở lên thì để lại số `[1]` `[2]` — mất dấu
    ý nào của file nào là mất đúng thứ khiến dẫn nguồn có ích.
    """
    labels: list[str] = []
    for match in _CITATION.finditer(text):
        label = _source_label(match[1])
        if label not in labels:
            labels.append(label)

    if not labels:
        return text, []
    if len(labels) == 1:
        return _CITATION.sub("", text), labels
    return (
        _CITATION.sub(lambda m: f" [{labels.index(_source_label(m[1])) + 1}]", text),
        labels,
    )


def _strip_project_prefix(text: str, project: str) -> str:
    """Bỏ "Dự án X:" mở đầu — tiêu đề đã nói rồi, nhắc lại là thừa một dòng.

    Vẫn phải làm dù compose.md đã dặn đừng viết: prompt là lời dặn, không phải
    ràng buộc, và checkpoint cũ còn giữ câu trả lời viết theo luật cũ.
    """
    if not project:
        return text
    pattern = rf"^\s*(?:dự\s*án\s+)?{re.escape(project)}\s*[:\-–—]?\s*"
    stripped = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)
    if stripped == text or not stripped:
        return text
    # Cắt xong hay còn lại "đang thực hiện..." — viết hoa cho ra đầu câu.
    return stripped[0].upper() + stripped[1:]


def _layout(text: str) -> str:
    """Xuống dòng cho dễ đọc: gạch đầu dòng đồng nhất, có khoảng thở trước danh sách."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        is_bullet = bool(_BULLET.match(line))
        if is_bullet:
            # Danh sách dính ngay dưới câu dẫn thì đọc như một khối chữ liền.
            if lines and lines[-1].strip() and not lines[-1].startswith("•"):
                lines.append("")
            line = _BULLET.sub("• ", line)
        lines.append(line)
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def _answer(data: dict[str, Any]) -> str:
    """Câu trả lời của bot client -> HTML ba vùng. Cắt bớt nếu quá dài.

    Telegram chặn ở 4096 ký tự và trả 400 — tin nhắn MẤT LUÔN, người dùng chỉ
    thấy im lặng. Đo trên chữ THUẦN (thẻ HTML thành entity, không tính vào giới
    hạn) và trừ sẵn phần tiêu đề với dòng nguồn, vì cắt ở giữa một thẻ mở là
    Telegram cũng trả 400.
    """
    text = str(data.get("text", "")).strip()
    if not text:
        return "Mình chưa có câu trả lời nào cho câu này."

    project = str(data.get("project_name") or "").strip()
    body, sources = _pull_sources(_strip_project_prefix(text, project))
    body = _layout(body)

    header = f"📁 {project}" if project else ""
    if not sources:
        footer = ""
    elif len(sources) == 1:
        footer = f"📄 Nguồn: {sources[0]}"
    else:
        footer = "\n".join(
            ["📄 Nguồn:"] + [f"[{i}] {s}" for i, s in enumerate(sources, 1)]
        )

    room = _MAX_ANSWER_CHARS - len(header) - len(footer)
    if len(body) > room:
        body = body[: max(room - len(_CLIPPED), 0)].rstrip() + _CLIPPED

    blocks = []
    if header:
        blocks.append(f"<b>{_esc(header)}</b>")
    blocks.append(_markdown(_esc(body)))
    if footer:
        blocks.append(f"<i>{_esc(footer)}</i>")
    return "\n\n".join(blocks)


def _client_failed(data: dict[str, Any]) -> str:
    reason = data.get("reason", "")
    return _CLIENT_FAIL_TEXT.get(
        reason, f"Mình chưa trả lời được câu này (mã lỗi: {reason})."
    )


def _project_list(data: dict[str, Any]) -> str:
    projects = data.get("projects") or []
    if not projects:
        return "Kho chưa có dự án nào. Gõ /duan <tên dự án> để tạo."
    lines = ["Danh sách dự án:"]
    lines += [f"• {p['name']} ({p['document_count']} tài liệu)" for p in projects]
    return "\n".join(lines)


# Chỉ kind cần rẽ nhánh mới nằm ở đây; còn lại là chuỗi mẫu trong _TEXT.
_HANDLER: dict[str, Callable[[dict[str, Any]], str]] = {
    "ingest_failed": _failed,
    "upload_rejected": _failed,
    "project_list": _project_list,
    # luồng client
    "answer": _answer,
    "client_failed": _client_failed,
}


# Kind nào dựng HTML. Mọi kind khác gửi chữ thuần, KHÔNG escape gì — nên thêm
# tên vào đây mà quên escape trong handler là tin nhắn bay màu ở Telegram.
_HTML_KINDS = frozenset({"answer"})


def parse_mode_of(event: Event) -> str | None:
    """parse_mode phải gửi kèm chuỗi mà `render` vừa trả về.

    Tách khỏi `render` để nó vẫn trả `str` như mọi nơi đang gọi. Nơi gửi phải
    dùng CẢ HAI cùng lúc: gửi HTML mà quên parse_mode thì người dùng đọc thấy
    thẻ `<b>`, còn gửi chữ thuần kèm parse_mode="HTML" thì Telegram trả 400.
    """
    return "HTML" if event.get("kind", "") in _HTML_KINDS else None


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
