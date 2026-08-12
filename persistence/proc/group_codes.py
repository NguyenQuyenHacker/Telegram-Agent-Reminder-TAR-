"""Cấp mã việc chạy số theo từng nhóm."""

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.task_code import derive_prefix, format_code, prefix_alternatives
from persistence.models.group_code import GroupCode
from persistence.pool import get_session


def _pick_free_prefix(session, group: str) -> str:
    """Tiền tố chưa nhóm nào dùng. Hai nhóm khác nhau không được trùng tiền tố,
    nếu không "TB-002" trỏ vào hai việc."""
    taken = set(session.exec(select(GroupCode.prefix)).all())
    base = derive_prefix(group)
    if base not in taken:
        return base
    for candidate in prefix_alternatives(group):
        if candidate not in taken:
            return candidate
    # Hết 26 phương án cho cùng một tiền tố gốc: không thể xảy ra ở quy mô này
    raise RuntimeError(f"Không còn tiền tố trống cho nhóm {group!r}")


def _next_code(session, group: str) -> str:
    """SELECT ... FOR UPDATE giữ dòng bộ đếm tới hết transaction, để hai lượt lưu
    song song không cùng đọc ra một next_seq."""
    counter = session.exec(
        select(GroupCode).where(GroupCode.group == group).with_for_update()
    ).first()
    if counter is None:
        counter = GroupCode(group=group, prefix=_pick_free_prefix(session, group))

    code = format_code(counter.prefix, counter.next_seq)
    counter.next_seq += 1
    session.add(counter)
    session.commit()
    return code


def allocate_code(group: str) -> str:
    """Mã kế tiếp của nhóm, ví dụ "TB-002". Nhóm chưa có thì tạo bộ đếm mới."""
    with get_session() as s:
        try:
            return _next_code(s, group)
        except IntegrityError:
            # Nhóm vừa được một lượt khác chèn xong giữa lúc ta đọc: đọc lại là
            # thấy dòng của họ, khoá được, và chạy tiếp bình thường.
            s.rollback()
            return _next_code(s, group)
