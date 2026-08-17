"""State của vòng sinh-kiểm-chạy SQL. Không dùng chung gì với ClientState.

`error` mang NGUYÊN VĂN thông báo của Postgres (hoặc lý do `validate` từ chối),
không phải một mã lỗi: nó đi thẳng vào prompt của `repair`, và "column ngay_ht
does not exist" là thứ model sửa được, còn "sql_error" thì không.
"""

import uuid
from typing import Any

from typing_extensions import TypedDict


class SqlState(TypedDict, total=False):
    question: str
    project_id: uuid.UUID
    # Câu SQL đang xét. `gen_sql` và `repair` cùng ghi vào đây.
    sql: str
    rows: list[dict[str, Any]]
    columns: list[str]
    # Lý do lần thử vừa rồi trượt. Rỗng/None nghĩa là chưa trượt lần nào.
    error: str | None
    # Số lần ĐÃ sinh SQL. `repair` tự tăng — router không ghi được state.
    attempt: int
    # `execute` chạy xong không lỗi.
    ok: bool
    # Model từ chối viết SQL vì bảng không có trường câu hỏi cần — mang LÝ DO,
    # không phải cờ bool. Khác hẳn `rows` rỗng ("có tra, không dòng nào khớp")
    # và khác `error` ("có câu SQL, nhưng nó hỏng"): đây là "câu hỏi này dữ
    # liệu không trả lời được", và `compose` phải nói ra đúng như vậy.
    unsupported: str | None
