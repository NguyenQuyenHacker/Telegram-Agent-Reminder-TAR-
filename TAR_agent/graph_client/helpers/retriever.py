"""Tìm chunk gần nghĩa nhất trong phạm vi MỘT dự án.

`project_id` là tham số BẮT BUỘC, không có giá trị mặc định, không nhận None.
Bỏ lọc dự án là trả lời câu hỏi của dự án này bằng tài liệu của dự án khác — và
câu trả lời đó trông vẫn rất thật.

TODO khi cài:
  - shared.embed.embed_query(question) -> vector
  - persistence.proc.chunks.search(project_id, vector, k)
  - Trả kèm NGUỒN: tên file + as_of_date. Câu trả lời phải trích dẫn được, nếu
    không người đọc không có cách nào kiểm chứng.
  - Cân nhắc ngưỡng khoảng cách: không có chunk nào đủ gần thì nói thẳng là kho
    không có, đừng trả về ba đoạn lạc đề cho LLM tán.
"""

import uuid
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    heading_path: str | None
    file_name: str
    as_of_date: date
    distance: float


async def search(
    project_id: uuid.UUID, question: str, k: int = 5
) -> list[RetrievedChunk]:
    raise NotImplementedError
