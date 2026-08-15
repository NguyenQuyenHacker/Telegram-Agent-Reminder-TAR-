"""State của vòng tra cứu. Không dùng chung gì với ClientState.

Hai trường câu hỏi vì chúng phục vụ hai việc khác nhau:
  - `question` là truy vấn ĐANG dùng, `rewrite` ghi đè nó mỗi vòng.
  - `original` là truy vấn agent đưa vào, không ai được sửa. `grade` chấm theo
    trường này — chấm theo `question` là sau một lần viết lại hỏng, cụm này tự
    chấm "đạt" cho đúng thứ nó vừa đi lạc.
"""

import uuid
from typing import Any

from typing_extensions import TypedDict


class SearchState(TypedDict, total=False):
    question: str
    original: str
    project_id: uuid.UUID
    # Số lần ĐÃ retrieve. `retrieve` tự tăng, không ai khác đụng vào.
    attempt: int
    docs: list[Any]  # list[RetrievedChunk]
    # `grade` chấm là đủ trả lời câu `original` hay chưa.
    ok: bool
