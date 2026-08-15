"""Schema và hàm thuần của vòng tra cứu. Không state, không LLM, không I/O.

Thân node nằm ở `graph.SearchGraph` — mỗi node cần một model riêng, mà model thì
dựng một lần lúc khởi tạo chứ không dựng lại mỗi lời gọi.
"""

from typing import Any

from pydantic import BaseModel, Field


class Grade(BaseModel):
    """Kết quả chấm CẢ LÔ passage trong một lượt LLM."""

    keep: list[int] = Field(
        description=(
            "Số thứ tự các đoạn thật sự liên quan tới câu hỏi, đếm từ 1. "
            "Không đoạn nào liên quan thì để danh sách rỗng."
        )
    )
    enough: bool = Field(
        description=(
            "true nếu những đoạn giữ lại đã đủ để trả lời câu hỏi. "
            "false nếu chúng chỉ chạm tới chủ đề mà không có dữ liệu cần tìm."
        )
    )
    reason: str = Field(description="Một câu tiếng Việt nói vì sao. Ngắn.")


def number_passages(docs: list[Any]) -> str:
    """Danh sách đoạn -> khối văn bản đánh số, để LLM trả về được chỉ số.

    Đánh số từ 1 chứ không từ 0: model đếm từ 1 và sẽ trả về số theo cách nó
    đọc thấy, bất kể prompt dặn gì. Nơi gọi trừ 1 lúc lọc.
    """
    blocks = []
    for index, doc in enumerate(docs, start=1):
        head = f"[{index}] {doc.file_name} · {doc.as_of_date}"
        if doc.heading_path:
            head += f" · {doc.heading_path}"
        blocks.append(f"{head}\n{doc.content}")
    return "\n\n".join(blocks)


def keep_only(docs: list[Any], keep: list[int]) -> list[Any]:
    """Lọc theo chỉ số 1-based mà LLM trả về, bỏ qua số nằm ngoài khoảng.

    Model bịa ra chỉ số `[7]` cho một lô 5 đoạn là chuyện có thật; im lặng bỏ
    qua thay vì ném IndexError giữa một lượt trả lời.
    """
    return [docs[i - 1] for i in dict.fromkeys(keep) if 1 <= i <= len(docs)]
