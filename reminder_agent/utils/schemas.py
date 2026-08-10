"""Schema mà LLM phải trả về (dùng cho with_structured_output)."""

from typing import Literal

from pydantic import BaseModel, Field


class ExtractedTask(BaseModel):
    group: str = Field(description="Tên nhóm dự án")
    content: str = Field(description="Nguyên văn dòng đầu việc")
    due_date: str | None = Field(
        default=None, description="Hạn dạng YYYY-MM-DD, null nếu không ghi"
    )
    priority: Literal["urgent", "normal"]


class ExtractionResult(BaseModel):
    tasks: list[ExtractedTask]


class DecisionResult(BaseModel):
    """Ý định của người dùng khi xem bảng đầu việc vừa trích.

    Không ưng ý tức là muốn sửa, nên chỉ có approved/edit chứ không có
    "rejected" riêng. abandoned là đường thoát: bỏ hẳn, không lưu gì.
    """

    status: Literal["approved", "edit", "abandoned", "unclear"]
    edit_request: str | None = Field(
        default=None, description="Chỗ cần sửa, null nếu người dùng chưa nói rõ"
    )
