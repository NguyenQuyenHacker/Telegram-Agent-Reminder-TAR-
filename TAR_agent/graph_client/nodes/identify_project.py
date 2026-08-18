"""Xác định người dùng đang hỏi về dự án nào. Schema và hàm thuần.

Thân node là `ClientGraph._identify_project` — nó cần model `identify` đã dựng
sẵn, mà model thì dựng một lần lúc khởi tạo graph.

VÌ SAO LÀ MỘT NODE LLM chứ không phải khớp chuỗi: người dùng gõ "ap truong thon"
cho dự án "📱 App Trưởng thôn", gõ tên dự án ở giữa câu, hoặc không gõ gì cả vì
lượt trước đã nói rồi. Khớp lỏng bằng LIKE/fuzzy giải quyết được ca một, chịu ca
hai, và ca ba thì nó không biết là nó không biết.

Đổi lại, LLM có thể trả về một `project_id` không tồn tại. Vì thế `validate_pick`
đối chiếu lại với chính danh sách vừa truy vấn — LLM đề xuất, CODE mới quyết.
"""

import logging
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

NO_PROJECTS_REPLY = (
    "Kho chưa có dự án nào nên mình chưa tra được gì. "
    "Nhờ quản trị viên nạp tài liệu trước nhé."
)


def ask_what_about(project_name: str) -> str:
    """Câu hỏi lại khi đã chốt dự án mà người dùng chưa hỏi gì.

    Dựng bằng CODE chứ không chỉ trông vào `reply` của LLM: `needs_question` là
    thứ cắt cả vòng tra cứu, nên nhánh đó không được phép kết thúc bằng một
    chuỗi rỗng nếu model quên viết câu hỏi lại.
    """
    return (
        f'Mình đã mở dự án "{project_name}". Bạn muốn biết gì — tiến độ, '
        "hạng mục, đơn vị thực hiện, hay mốc thời gian?"
    )


class ProjectPick(BaseModel):
    project_id: str | None = Field(
        default=None,
        description=(
            "project_id chép NGUYÊN VĂN từ danh sách được cấp. "
            "Không chắc chắn thì để null."
        ),
    )
    project_name: str | None = Field(
        default=None, description="Tên dự án tương ứng, chép nguyên văn."
    )
    remaining_question: str | None = Field(
        default=None,
        description="Câu hỏi sau khi bỏ phần nêu tên dự án. Null nếu không tách được.",
    )
    needs_question: bool = Field(
        default=False,
        description=(
            "true khi đã chốt được dự án NHƯNG tin nhắn chưa hỏi điều gì cụ thể "
            "để tra (ví dụ 'cho mình hỏi về dự án X đi'). Có câu hỏi thật thì false."
        ),
    )
    reply: str | None = Field(
        default=None,
        description=(
            "Câu trả lời tiếng Việt gửi thẳng cho người dùng. Điền khi project_id "
            "là null, hoặc khi needs_question là true."
        ),
    )


def last_question(chat_history: list[Any]) -> str:
    """Nguyên văn tin nhắn cuối của người dùng."""
    for message in reversed(chat_history or []):
        if isinstance(message, HumanMessage):
            return str(message.content).strip()
    return ""


def describe_projects(projects: list[Any]) -> str:
    """Danh sách dự án đưa vào prompt. Có id vì LLM phải chép lại đúng chuỗi đó."""
    return "\n".join(
        f"- {p.project_id} | {p.name} ({p.document_count} tài liệu)" for p in projects
    )


def describe_current(project_id: uuid.UUID | None, project_name: str | None) -> str:
    """Dự án đang giữ từ lượt trước — nguyên liệu của luật 4 trong prompt."""
    if project_id is None:
        return "(chưa có — người dùng chưa nói dự án nào, hoặc vừa gõ /start, /huy)"
    return f"{project_id} | {project_name}"


def validate_pick(
    pick: ProjectPick, projects: list[Any]
) -> tuple[uuid.UUID | None, str | None]:
    """LLM đề xuất, code quyết. Trả (project_id, project_name) hoặc (None, None).

    Một `project_id` bịa ra mà lọt xuống dưới thì tầng tra chạy trên một dự án
    rỗng và trả "kho không có" — nghe hợp lý, và không ai biết là sai chỗ nào.
    """
    if not pick.project_id:
        return None, None

    known = {str(p.project_id): p for p in projects}
    project = known.get(str(pick.project_id).strip())
    if project is None:
        log.warning(
            "identify_project trả project_id không có trong kho: %r", pick.project_id
        )
        return None, None
    return project.project_id, project.name
