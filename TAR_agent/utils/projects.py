"""Phân giải tên dự án người dùng gõ -> project_id.

Dùng chung cho cả hai luồng: admin nạp file phải gắn dự án, client hỏi phải lọc
theo dự án.

Trả về kiểu BA trạng thái thay vì tự chọn bừa khi mơ hồ. Đoán sai dự án là trả
lời bằng tài liệu của dự án khác — mà câu trả lời đó trông vẫn rất thật, nên
không ai phát hiện ra.

Hàm async, bọc lời gọi DB đồng bộ bằng asyncio.to_thread: đây là lõi, nó chạy
trong event loop chung với hai bot.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal

from persistence.proc import projects as proc


@dataclass(frozen=True)
class ProjectBrief:
    project_id: uuid.UUID
    name: str
    document_count: int


@dataclass(frozen=True)
class ProjectMatch:
    status: Literal["found", "not_found", "ambiguous"]
    project_id: uuid.UUID | None = None
    name: str | None = None
    candidates: tuple[str, ...] = ()


async def resolve_project(name: str) -> ProjectMatch:
    """KHÔNG DÙNG Ở LUỒNG NÀO. Giữ lại làm sẵn cho nhánh admin sau này.

    Luồng client xác định dự án bằng một node LLM, không bằng khớp chuỗi — xem
    graph_client/nodes/identify_project.py. Người dùng gõ tên dự án lẫn giữa câu,
    sai chính tả, thiếu dấu, hoặc không gõ gì cả vì lượt trước đã nói rồi; khớp
    lỏng chịu cả ba ca sau.

    Luồng admin thì chọn dự án bằng bàn phím inline (graph_admin/nodes/
    ask_project.py), cũng không cần hàm này.

    Kéo theo `proc.find_projects` và `text.ilike_pattern` cũng đang mồ côi.

    ---

    Khớp chính xác trước, không có thì tìm lỏng.

    Khớp chính xác đi qua `name_uuid` nên "📱 App Trưởng thôn" và
    "2/ app trưởng thôn" đều rơi về đúng một dòng mà không cần LIKE.
    """
    from TAR_agent.utils.text import name_uuid

    exact = await asyncio.to_thread(proc.get_project, name_uuid(name))
    if exact is not None:
        return ProjectMatch("found", exact.project_id, exact.name)

    loose = await asyncio.to_thread(proc.find_projects, name)
    if len(loose) == 1:
        return ProjectMatch("found", loose[0].project_id, loose[0].name)
    if len(loose) > 1:
        return ProjectMatch("ambiguous", candidates=tuple(p.name for p in loose))
    return ProjectMatch("not_found")


async def create_project(name: str) -> tuple[ProjectBrief, bool]:
    """Tạo dự án, hoặc trả về dự án đã có. Cờ thứ hai cho biết có phải vừa tạo.

    Chỗ DUY NHẤT trong hệ thống tạo dự án — nạp file không bao giờ tự tạo, nếu
    không một cú gõ nhầm sinh ra dự án rác đã có tài liệu nằm trong.
    """
    project, created = await asyncio.to_thread(proc.create_project, name)
    invalidate_projects()
    return ProjectBrief(project.project_id, project.name, 0), created


# Danh sách dự án đọc ở MỌI lượt hỏi (node `identify_project`) nhưng chỉ đổi khi
# admin tạo dự án hoặc nạp/xoá tài liệu — tức là hiếm hơn hàng trăm lần.
_CACHE: list[ProjectBrief] | None = None


def invalidate_projects() -> None:
    """Gọi sau MỌI thay đổi tới danh sách dự án hoặc số tài liệu của chúng.

    Vô hiệu tường minh chứ không đặt TTL: bên GHI biết chính xác lúc nào nó
    ghi, còn TTL thì vừa giữ dữ liệu cũ lâu hơn cần thiết vừa đọc lại sớm hơn
    cần thiết. Cùng lý do đã ghi ở `chunks.corpus_fingerprint`.
    """
    global _CACHE
    _CACHE = None


async def list_projects() -> list[ProjectBrief]:
    """Mọi dự án kèm số tài liệu, sắp theo tên. CHỈ ĐỌC — đừng sửa list trả về.

    Trả về chính list trong cache chứ không phải bản sao: hai nơi gọi đều chỉ
    đọc để dựng prompt hoặc bàn phím.
    """
    global _CACHE
    if _CACHE is None:
        rows = await asyncio.to_thread(proc.list_projects)
        _CACHE = [ProjectBrief(p.project_id, p.name, n) for p, n in rows]
    return _CACHE
