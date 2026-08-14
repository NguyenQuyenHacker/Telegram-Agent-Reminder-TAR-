"""Truy vấn bảng project. Hàm ĐỒNG BỘ — nơi gọi bọc asyncio.to_thread.

`project_id` suy thẳng từ tên bằng `TAR_agent.utils.text.name_uuid`, không phải số tự
tăng: biết tên là biết id, khỏi tra DB một lượt chỉ để lấy khoá.
"""

import uuid

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import select

from persistence.models import Project, SourceDocument
from persistence.pool import get_session
from TAR_agent.utils.text import LIKE_ESCAPE, ilike_pattern, name_uuid, normalize_name


def create_project(name: str) -> tuple[Project, bool]:
    """Tạo dự án nếu chưa có. Trả về (dự án, có phải vừa tạo không).

    Dùng ON CONFLICT DO NOTHING rồi SELECT, không phải SELECT-rồi-INSERT: hai
    admin gõ /duan cùng lúc thì đường thứ hai đua và ném IntegrityError.

    `name` giữ nguyên như admin gõ để hiển thị; `normalized_name` mới là thứ
    quyết định trùng hay không.
    """
    display = " ".join(name.split())
    normalized = normalize_name(name)
    if not normalized:
        raise ValueError("Tên dự án rỗng sau khi chuẩn hoá")

    project_id = name_uuid(name)
    with get_session() as session:
        result = session.exec(
            pg_insert(Project)
            .values(
                project_id=project_id,
                name=display,
                normalized_name=normalized,
            )
            .on_conflict_do_nothing(index_elements=["normalized_name"])
            .returning(Project.project_id)
        )
        created = result.first() is not None
        session.commit()

        project = session.get(Project, project_id)
        if project is None:
            # normalized_name đã có nhưng mang project_id khác — chỉ xảy ra nếu
            # ai đó chèn tay không qua name_uuid. Tra lại theo tên chuẩn hoá.
            project = session.exec(
                select(Project).where(Project.normalized_name == normalized)
            ).one()
        return project, created


def get_project(project_id: uuid.UUID) -> Project | None:
    with get_session() as session:
        return session.get(Project, project_id)


def find_projects(keyword: str) -> list[Project]:
    """Khớp lỏng theo tên. Escape ký tự đại diện, không thì gõ "100%" là quét sạch bảng."""
    with get_session() as session:
        return list(
            session.exec(
                select(Project)
                .where(
                    Project.normalized_name.ilike(  # type: ignore[attr-defined]
                        ilike_pattern(normalize_name(keyword)), escape=LIKE_ESCAPE
                    )
                )
                .order_by(Project.name)
            ).all()
        )


def list_projects() -> list[tuple[Project, int]]:
    """Mọi dự án kèm số tài liệu. Dùng cho bàn phím chọn dự án và lệnh /duan.

    outerjoin chứ không join: dự án vừa tạo, chưa có tài liệu nào, vẫn phải hiện
    ra trong bàn phím — nếu không thì không bao giờ nạp được file đầu tiên.
    """
    with get_session() as session:
        rows = session.exec(
            select(Project, func.count(SourceDocument.document_id))
            .outerjoin(SourceDocument, SourceDocument.project_id == Project.project_id)  # type: ignore[arg-type]
            .group_by(Project.project_id)  # type: ignore[arg-type]
            .order_by(Project.name)
        ).all()
        return [(project, count) for project, count in rows]
