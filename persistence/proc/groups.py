"""Nhóm dự án: tra theo tên, và cấp mã việc chạy số trong nhóm."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.task_code import (
    derive_prefix,
    format_code,
    group_uuid,
    normalize_group,
    prefix_alternatives,
)
from persistence.models.project_group import ProjectGroup
from persistence.pool import get_session


def _pick_free_prefix(session: Session, name: str) -> str:
    """Tiền tố chưa nhóm nào dùng. Hai nhóm khác nhau không được trùng tiền tố,
    nếu không "TB-002" trỏ vào hai việc."""
    taken = set(session.exec(select(ProjectGroup.prefix)).all())
    base = derive_prefix(name)
    if base not in taken:
        return base
    for candidate in prefix_alternatives(name):
        if candidate not in taken:
            return candidate
    # Hết 26 phương án cho cùng một tiền tố gốc: không thể xảy ra ở quy mô này
    raise RuntimeError(f"Không còn tiền tố trống cho nhóm {name!r}")


def _create_group(session: Session, group_id: uuid.UUID, name: str) -> ProjectGroup:
    group = ProjectGroup(
        group_id=group_id,
        name=name,
        normalized_name=normalize_group(name),
        prefix=_pick_free_prefix(session, name),
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def get_or_create_group(name: str) -> ProjectGroup:
    """Nhóm ứng với tên này, tạo mới nếu chưa có.

    group_id suy thẳng từ tên đã chuẩn hoá nên mọi biến thể cách viết — hoa
    thường, có dấu hay không, còn sót "2/" đầu dòng — đều rơi về đúng một dòng.
    Tên hiển thị thì cập nhật theo lần gặp gần nhất.
    """
    group_id = group_uuid(name)
    with get_session() as session:
        group = session.get(ProjectGroup, group_id)
        if group is None:
            try:
                return _create_group(session, group_id, name)
            except IntegrityError:
                # Một lượt khác vừa chèn xong nhóm này giữa lúc ta đọc.
                session.rollback()
                group = session.get(ProjectGroup, group_id)
                if group is None:
                    raise

        if group.name != name:
            group.name = name
            session.add(group)
            session.commit()
            session.refresh(group)
        return group


def allocate_code(group_id: uuid.UUID) -> str:
    """Mã kế tiếp của nhóm, ví dụ "TB-002".

    SELECT ... FOR UPDATE giữ dòng nhóm tới hết transaction, để hai lượt lưu
    song song không cùng đọc ra một next_seq.
    """
    with get_session() as session:
        group = session.exec(
            select(ProjectGroup)
            .where(ProjectGroup.group_id == group_id)
            .with_for_update()
        ).first()
        if group is None:
            # upsert_task luôn gọi get_or_create_group trước, nên tới đây mà
            # trống nghĩa là ai đó xoá dòng nhóm giữa chừng.
            raise LookupError(f"Không có nhóm {group_id}")

        code = format_code(group.prefix, group.next_seq)
        group.next_seq += 1
        session.add(group)
        session.commit()
        return code
