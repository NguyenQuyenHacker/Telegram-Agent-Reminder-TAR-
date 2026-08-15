"""State của graph admin — hợp đồng vào/ra với tầng Telegram.

Ba điểm cố ý:
  - KHÔNG có `chat_id`: nó sống trong `thread_id`, do tầng webhook đặt.
  - Có `outbox`: node append event, tầng Telegram render và gửi. Lõi test được
    mà không cần mock aiogram.
  - `UploadedFile` mang ĐƯỜNG DẪN chứ không mang bytes — LangGraph ghi
    checkpoint sau mỗi node, giữ bytes là đẩy tới 20MB vào bảng checkpoint.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain_core.documents import Document
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# typing_extensions chứ không phải typing: pydantic không dựng được schema từ
# typing.TypedDict trên Python < 3.12, mà langgraph cần schema đó.
from typing_extensions import TypedDict


@dataclass(frozen=True)
class UploadedFile:
    """File admin gửi lên, đã nằm trên đĩa. `path` là file TẠM — `report` xoá nó."""

    file_name: str
    path: Path
    size_bytes: int


class Event(TypedDict):
    """Một thứ cần nói với người dùng. `kind` quyết định cách render."""

    kind: str
    data: dict[str, Any]


# "created"   nạp mới
# "updated"   ghi đè bản cũ cùng tên
# "unchanged" nội dung y hệt bản đang có -> không embed, không ghi
# "cancelled" admin bấm Huỷ ở câu hỏi ghi đè
Outcome = Literal["created", "updated", "unchanged", "cancelled"]


class AdminState(TypedDict, total=False):
    # --- đầu vào, tầng Telegram điền ---
    messages: Annotated[list[AnyMessage], add_messages]
    # Có file đính kèm -> nhánh nạp. None -> nhánh tin nhắn chữ.
    upload: UploadedFile | None
    # Telegram user id, để ghi vào source_document.uploaded_by
    uploaded_by: int

    # --- ask_project điền ---
    project_id: uuid.UUID | None
    project_name: str | None

    # --- check_file điền ---
    base_name: str
    content_sha256: str
    existing_id: uuid.UUID | None
    existing_chunk_count: int
    outcome: Outcome | None

    # --- parse điền ---
    file_kind: str
    documents: list[Document]
    chunks: list[Document]
    as_of_date: date

    # --- extract điền ---
    # Dòng ĐÃ qua rowcheck, sẵn sàng ghi thẳng xuống data.cong_viec.
    rows: list[Any]  # list[CheckedRow]
    # Ghi chú cho admin: lý do từng dòng bị loại, CỘNG cảnh báo lệch so_ngay của
    # những dòng vẫn được giữ. Vì vậy độ dài của nó KHÔNG phải số dòng bị loại.
    rows_rejected: list[str]
    rows_rejected_count: int
    # Ánh xạ cột đã dùng cho mỗi sheet .xlsx. `report` in ra để admin soát —
    # ánh xạ header hỏng thì hỏng cho CẢ sheet, và không có cách nào khác biết.
    column_map: dict[str, str]
    # MÃ lỗi của riêng khâu trích. Tách khỏi `error` vì nó KHÔNG huỷ lượt nạp.
    extract_error: str | None

    # --- store điền ---
    document_id: uuid.UUID | None
    chunk_count: int
    row_count: int

    # --- xuyên suốt ---
    # MÃ lỗi, không phải câu chữ. render.py mới dịch sang tiếng Việt.
    error: str | None
    outbox: list[Event]
