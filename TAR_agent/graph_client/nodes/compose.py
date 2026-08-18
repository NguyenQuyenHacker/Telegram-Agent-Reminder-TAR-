"""Soạn câu trả lời cuối. Schema và hàm thuần; thân node là `ClientGraph._compose`.

Node này nhận một khối văn bản dựng từ các ToolMessage chứ không nhận nguyên
`messages`: cái nó cần là DỮ LIỆU TRA ĐƯỢC, không phải quá trình đi tìm.

Mỗi lượt tra chạy cả nhánh bảng lẫn nhánh tài liệu nên một payload có thể mang
cùng lúc kết quả truy vấn, đoạn tài liệu, và lời từ chối của nhánh bảng. Luật
phân xử nằm ở prompts/client_system/compose.md; `render_tool_results` chỉ lo bày
đủ ba thứ đó thành ba khối rõ ràng.
"""

import json
from typing import Any

from pydantic import BaseModel, Field

NO_PASSAGE = 'KẾT QUẢ TRA: không có đoạn nào (status "empty").'

# Nhãn của khối `unsupported`. Phải khác hẳn NO_PASSAGE về chữ, vì hai thứ này
# dẫn tới hai câu trả lời khác nhau: "kho chưa có tài liệu" (nạp thêm file là
# xong) và "dữ liệu không có trường này" (nạp bao nhiêu cũng không xong).
UNSUPPORTED_HEAD = "KHÔNG TRA ĐƯỢC · dữ liệu không có trường mà câu hỏi cần"


class Compose(BaseModel):
    """Câu trả lời cuối, kèm bản tự chấm của chính model vừa viết nó."""

    answer: str = Field(description="Câu trả lời tiếng Việt gửi thẳng cho người dùng.")
    verdict: str = Field(
        description=(
            '"ok" nếu mọi ý đều truy được về một đoạn tài liệu, '
            '"insufficient" nếu không.'
        )
    )
    missing: str = Field(
        default="", description="Còn thiếu dữ liệu gì. Rỗng khi verdict là 'ok'."
    )


def tool_payload(message: Any) -> dict | None:
    """Nội dung một ToolMessage -> dict, hoặc None nếu không đọc được.

    Trả None chứ không đoán: nơi gọi phải tự quyết định coi đó là "có dữ liệu"
    hay "không", và hai chỗ gọi đang quyết định khác nhau.
    """
    raw = message.content if isinstance(message.content, str) else str(message.content)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _render_rows(payload: dict) -> str:
    """Kết quả nhánh bảng -> bảng chữ cho prompt.

    Dạng bảng chứ không phải JSON thô: JSON lặp tên khoá ở mọi dòng, model đọc
    tốn token gấp đôi mà không hiểu rõ hơn. Kèm câu SQL đã chạy để model biết con
    số này ĐƯỢC TÍNH RA, không phải chép từ đâu — đúng thứ nó cần để không tự
    thấy mình đang bịa khi trả lời một câu hỏi có `SUM()`.
    """
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not rows:
        return ""
    if not columns:
        columns = list(rows[0].keys())

    lines = [f"[Kết quả truy vấn bảng lịch công việc · SQL: {payload.get('sql', '')}]"]
    lines.append(" | ".join(str(c) for c in columns))
    lines += [
        " | ".join("" if row.get(c) is None else str(row.get(c)) for c in columns)
        for row in rows
    ]
    return "\n".join(lines)


def unsupported_note(payload: dict) -> str:
    """Lời từ chối của nhánh bảng, hoặc chuỗi rỗng.

    Tên KHÁC `subgraph_sql.nodes.unsupported_reason` dù cùng nói về một thứ: hàm
    kia đọc câu TRẢ LỜI CỦA MODEL, hàm này đọc PAYLOAD đã đóng gói. Trùng tên thì
    hai import trong cùng một file là một cái bẫy.

    Đọc hai hình dạng vì payload đổi khuôn khi hai tool gộp làm một:
      `status: "unsupported"` + `reason`         khuôn cũ, một tool một nhánh
      `table: "unsupported"` + `table_reason`    khuôn mới, một payload hai nhánh
    """
    if payload.get("status") == "unsupported":
        return str(payload.get("reason") or "").strip()
    if payload.get("table") == "unsupported":
        return str(payload.get("table_reason") or "").strip()
    return ""


def render_tool_results(tool_messages: list[Any]) -> str:
    """Mọi thứ tra được trong lượt -> một khối văn bản cho prompt của compose.

    BA khối rời nhau, không khối nào loại trừ khối nào: một payload có thể vừa
    mang lời từ chối của nhánh bảng vừa mang đoạn tài liệu tra được, nên thoát
    sớm ở khối từ chối là vứt luôn phần đang cứu được lượt hỏi đó.

    Trùng đoạn thì bỏ — vòng revise tra lần thứ hai và hai lần đó hay trả về
    chung vài đoạn.
    """
    blocks: list[str] = []
    seen: set[str] = set()

    for message in tool_messages:
        payload = tool_payload(message)
        if payload is None:
            # Không đọc được thì đưa nguyên văn — thà thừa còn hơn nuốt mất một
            # kết quả tra rồi để compose trả lời "kho không có".
            blocks.append(str(message.content))
            continue

        # Lời TỪ CHỐI của nhánh bảng. Không dựng khối này thì lý do thật ("bảng
        # không có cột % hoàn thành") biến mất và compose rơi vào NO_PASSAGE.
        if reason := unsupported_note(payload):
            block = f"[{UNSUPPORTED_HEAD}]\n{reason}"
            if block not in seen:
                seen.add(block)
                blocks.append(block)

        for passage in payload.get("passages") or []:
            content = str(passage.get("content", ""))
            if not content or content in seen:
                continue
            seen.add(content)
            head = f"[{passage.get('file_name', '')} · {passage.get('as_of_date', '')}"
            if passage.get("heading_path"):
                head += f" · {passage['heading_path']}"
            blocks.append(f"{head}]\n{content}")

        table = _render_rows(payload)
        if table and table not in seen:
            seen.add(table)
            blocks.append(table)

    return "\n\n".join(blocks) if blocks else NO_PASSAGE
