"""Cưỡng chế: mọi CON SỐ và TÊN FILE trong câu trả lời phải có thật trong đoạn
tra được.

Vì sao cần lớp này dù `compose` đã tự chấm: model vừa viết xong rồi tự hỏi "bản
này có bịa không" thì nó sẽ nói không. `verdict` một mình không đáng tin. Đây là
thứ duy nhất trong luồng kiểm được bằng máy.

RANH GIỚI của nó, ghi rõ để không ai tưởng nhầm là đã an toàn: nó chặn bịa SỐ,
không chặn bịa Ý. "doanh thu 1250" và "doanh thu GIẢM 1250" đều lọt qua như
nhau, vì con số 1250 có thật trong tài liệu. Muốn chặn phần ý thì phải là một
lượt LLM độc lập, không phải regex.

Module thuần: không state, không I/O, không LLM.
"""

import json
import re
from typing import Any

from TAR_agent.utils.text import DATE_RE

# Con số: chuỗi chữ số có thể xen dấu phân cách. Không nuốt khoảng trắng ở đây —
# "1 250" được ghép lại ở bước tiền xử lý, chứ để regex ăn khoảng trắng thì
# "5 hạng mục 3" thành một số.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# "1 250 000" -> "1250000". Chỉ ghép khi vế sau đúng 3 chữ số, đó là dấu hiệu
# của phân cách hàng nghìn kiểu Việt Nam.
_SPACED_THOUSANDS = re.compile(r"(?<=\d)\s(?=\d{3}(?!\d))")

# Dấu phân cách hàng nghìn: đứng giữa hai cụm chữ số, vế sau đúng 3 chữ số.
_THOUSAND_SEP = re.compile(r"(?<=\d)[.,](?=\d{3}(?!\d))")

# Ngày tháng: mẫu dùng CHUNG với graph_admin/helpers/rowcheck.py, khai ở
# TAR_agent/utils/text.py. Hai bản chép tay sẽ lệch nhau sau lần sửa đầu tiên.
_DATE = DATE_RE

# "Tháng 6", "quý 2", "năm 2026" — số ở đây là mốc thời gian, không phải số liệu.
_PERIOD = re.compile(r"\b(?:tháng|quý|năm|tuần|q)\s*\d{1,4}\b", re.IGNORECASE)

# Số thứ tự đầu dòng: "1. ", "2) ", "- 3. "
_LIST_ORDINAL = re.compile(r"^[\s\-•*]*\d+[.)]\s", re.MULTILINE)

# Tên file được dẫn nguồn. Đuôi khớp những gì kho nhận được (xem
# app/telegram/download.py) cộng vài đuôi model hay bịa ra.
_FILE_NAME = re.compile(r"[^\s\[\]()·|]+\.(?:xlsx|xls|txt|pdf|docx|csv)", re.IGNORECASE)


def _canon(token: str) -> str:
    """Một con số -> dạng chuẩn để so sánh.

    `1.250` / `1,250` / `1 250` -> `1250`; `12,5` -> `12.5`; `40.0` -> `40`.

    Phép chuẩn hoá này KHÔNG cần đúng về mặt ngữ nghĩa, nó chỉ cần NHẤT QUÁN:
    cả hai vế (câu trả lời và tài liệu) đi qua đúng hàm này. `0.750` bị hiểu
    thành `0750` cũng không sao — miễn là tài liệu viết `0.750` thì nó cũng ra
    `0750` và hai bên vẫn khớp. Cái giá thật sự là va chạm: hai số khác nhau
    dồn về cùng một chuỗi thì một câu bịa lọt lưới. Chấp nhận được, vì lưới này
    là lớp chặn thô chứ không phải bằng chứng.
    """
    text = _THOUSAND_SEP.sub("", token).replace(",", ".")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.lstrip("0") or "0"


def _numbers(text: str) -> set[str]:
    """Mọi con số ĐÁNG KIỂM trong một đoạn văn.

    Bỏ trước khi trích: ngày tháng, mốc kỳ (tháng 6, quý 2), số thứ tự đầu
    dòng. Ba thứ này model tự sinh ra hợp lệ trong lúc trình bày, bắt lỗi chúng
    là báo động giả ở gần như mọi câu trả lời.
    """
    stripped = _LIST_ORDINAL.sub("", text)
    stripped = _DATE.sub(" ", stripped)
    stripped = _PERIOD.sub(" ", stripped)
    stripped = _SPACED_THOUSANDS.sub("", stripped)

    found = {_canon(match.group()) for match in _NUMBER.finditer(stripped)}
    # Số một chữ số đứng một mình ("3 hạng mục", "2 tuần") là chuyện đếm và
    # diễn đạt, không phải số liệu chép từ tài liệu.
    return {number for number in found if len(number) > 1}


def _tool_text(tool_messages: list[Any]) -> str:
    """Gộp nội dung mọi ToolMessage của lượt này thành một khối văn bản.

    Content của ToolMessage là dict đã serialize; json.loads rồi ghép lại phần
    chữ để dấu ngoặc kép và tên khoá JSON không bị đếm nhầm thành nội dung.

    Đọc CẢ HAI nhánh của payload — `passages` và `rows`. Bỏ sót `rows` là hỏng
    theo kiểu rất khó lần: mọi con số ra từ bảng đóng góp 0 vào tập số hợp lệ,
    nên `check` coi chúng là bịa, `compose` ép verdict thành "insufficient", vòng
    revise quay tới lúc chạm trần, rồi `respond` dán câu rào vào chính câu trả
    lời ĐÚNG của nó.
    """
    parts: list[str] = []
    for message in tool_messages:
        raw = message.content if isinstance(message.content, str) else str(message.content)
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            parts.append(raw)
            continue
        if not isinstance(payload, dict):
            continue
        for passage in payload.get("passages") or []:
            parts.append(str(passage.get("content", "")))
            parts.append(str(passage.get("file_name", "")))
            parts.append(str(passage.get("as_of_date", "")))
            parts.append(str(passage.get("heading_path") or ""))
        # Dòng SQL: gộp mọi GIÁ TRỊ, không gộp tên cột. Tên cột do model tự đặt
        # trong câu SELECT nên đưa chúng vào tập nguồn là để model tự cấp phép
        # cho chính mình — `SELECT 1234 AS x` sẽ hợp thức hoá số 1234.
        for row in payload.get("rows") or []:
            values = row.values() if isinstance(row, dict) else row
            parts += [str(value) for value in values if value is not None]
    return "\n".join(parts)


def check(answer: str, tool_messages: list[Any], question: str) -> str | None:
    """None nếu sạch; ngược lại trả LÝ DO (tiếng Việt) để đá ngược về agent.

    Chuỗi trả về đi thẳng vào `missing` của state, tức là vào system prompt của
    vòng sau — nên nó phải đọc được như một lời dặn, không phải mã lỗi.
    """
    if not answer.strip():
        return "Câu trả lời rỗng."

    source = _tool_text(tool_messages)

    # Tên file phải có thật. Đây là chỗ bịa nguy hiểm nhất: dẫn nguồn sai làm
    # câu trả lời trông ĐÁNG TIN HƠN, và người đọc không có cách nào tra lại.
    known_files = {name.lower() for name in _FILE_NAME.findall(source)}
    for cited in _FILE_NAME.findall(answer):
        if cited.lower() not in known_files:
            return (
                f"Câu trả lời dẫn nguồn '{cited}' nhưng không đoạn tài liệu nào "
                f"đến từ file đó. Chỉ được dẫn đúng tên file trong kết quả tra."
            )

    allowed = _numbers(source) | _numbers(question)
    invented = sorted(_numbers(answer) - allowed)
    if invented:
        return (
            f"Các số {', '.join(invented)} không có trong đoạn tài liệu nào, cũng "
            f"không có trong kết quả truy vấn nào. Chép lại số nguyên văn từ "
            f"nguồn — cấm tự tính, tự làm tròn, tự quy đổi."
        )
    return None
