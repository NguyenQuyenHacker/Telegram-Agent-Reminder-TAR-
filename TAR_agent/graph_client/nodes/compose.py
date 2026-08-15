"""Soạn câu trả lời cuối. Schema và hàm thuần.

Thân node là `ClientGraph._compose`.

Node này KHÔNG nhận lại nguyên `messages` của vòng ReAct. Nó nhận một khối văn
bản dựng từ các ToolMessage. Hai lý do:

  - Model của `compose` không bind tool. Đưa cho nó lịch sử có phần gọi hàm là
    đưa một hội thoại nói về những công cụ nó không có.
  - Cái nó cần là ĐOẠN TÀI LIỆU, không phải quá trình đi tìm. Khối văn bản dựng
    ở đây là đúng thứ đó, và dựng bằng code nên hình dạng cố định.
"""

import json
from typing import Any

from pydantic import BaseModel, Field

NO_PASSAGE = 'KẾT QUẢ TRA: không có đoạn nào (status "empty").'


class Compose(BaseModel):
    """Câu trả lời cuối, kèm bản tự chấm của chính model vừa viết nó."""

    answer: str = Field(description="Câu trả lời tiếng Việt gửi thẳng cho người dùng.")
    verdict: str = Field(
        description='"dat" nếu mọi ý đều truy được về một đoạn tài liệu, "thieu" nếu không.'
    )
    missing: str = Field(
        default="", description="Còn thiếu dữ liệu gì. Rỗng khi verdict là 'dat'."
    )


def tool_payload(message: Any) -> dict | None:
    """Nội dung một ToolMessage -> dict, hoặc None nếu không đọc được.

    `ToolNode` serialize giá trị trả về của tool thành JSON. Không đọc được thì
    trả None chứ không đoán: nơi gọi phải tự quyết định coi đó là "có dữ liệu"
    hay "không", và hai chỗ gọi đang quyết định khác nhau.
    """
    raw = message.content if isinstance(message.content, str) else str(message.content)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _render_rows(payload: dict) -> str:
    """Kết quả `query_data` -> bảng chữ cho prompt.

    Đưa dạng bảng chứ không đưa JSON thô: JSON có dấu ngoặc và tên khoá lặp
    lại ở mọi dòng, model đọc nó tốn token gấp đôi mà không hiểu rõ hơn.

    Kèm luôn câu SQL đã chạy: nó cho model biết con số này ĐƯỢC TÍNH RA chứ
    không phải chép từ đâu — đúng cái nó cần biết để không tự thấy mình đang
    bịa khi trả lời một câu hỏi có `SUM()`.
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


def render_tool_results(tool_messages: list[Any]) -> str:
    """Mọi thứ tra được trong lượt -> một khối văn bản cho prompt của compose.

    Đọc CẢ HAI hình dạng payload: `passages` của `search_docs` và `rows` của
    `query_data`. Bỏ sót nhánh thứ hai thì `compose` soạn câu trả lời từ một
    băng trống và kết luận "kho không có" ngay sau khi SQL vừa trả về 47 dòng.

    Trùng đoạn thì bỏ: agent gọi tool nhiều lần cho câu hỏi nhiều ý, và các lần
    đó hay trả về chung vài đoạn. Lặp lại một đoạn ba lần trong prompt làm model
    tưởng nó quan trọng gấp ba.
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
