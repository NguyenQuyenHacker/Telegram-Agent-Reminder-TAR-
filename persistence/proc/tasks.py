import uuid
from datetime import date, datetime, timedelta, timezone

from sqlmodel import col, or_, select

from app.core.config import settings
from app.core.task_code import parse_code
from persistence.models.project_group import ProjectGroup
from persistence.models.task import Priority, Task, TaskStatus
from persistence.pool import get_session
from persistence.proc.groups import allocate_code


# Người dùng nhắc tới một việc thì chỉ vài ứng viên là đủ để hỏi lại cho rõ;
# nhiều hơn nữa thì bảng chọn dài quá, đọc trên Telegram không nổi.
_REFERENCE_MATCH_LIMIT = 5
# Trần an toàn cho tool query_tasks: LLM không được kéo cả bảng vào context.
_QUERY_RESULT_LIMIT = 50

# Trạng thái cuối <-> cột mốc thời gian đi kèm. Hai thứ này phải luôn khớp nhau:
# status=done mà done_at rỗng thì R8 không biết còn trong cửa sổ hoàn tác không,
# còn done_at sót lại trên một dòng đã hủy thì đó là dấu vết của trạng thái cũ,
# đọc lên là hiểu sai lịch sử của việc đó.
_CLOSED_AT_FIELD = {
    TaskStatus.done: "done_at",
    TaskStatus.cancelled: "cancelled_at",
}

# Ký tự escape cho ILIKE. Chuỗi người dùng gõ đi vào mẫu LIKE, không vô hiệu
# '%' và '_' thì "100%" thành mẫu khớp mọi thứ và tool quét sạch bảng.
_LIKE_ESCAPE = "\\"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _undo_deadline() -> datetime:
    """R8: mốc sớm nhất còn được hoàn tác."""
    return _utcnow() - timedelta(hours=settings.undo_window_hours)


def _ilike_pattern(text: str) -> str:
    """Chuỗi người dùng gõ -> mẫu khớp lỏng, đã vô hiệu ký tự đại diện của LIKE."""
    escaped = (
        text.strip()
        .replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


def get_due_tasks(now: datetime) -> list[Task]:
    """Chỉ việc đang chờ: xong hoặc đã hủy thì không nhắc nữa.

    Việc CON không vào lượt quét. Chúng là các đầu mục bên trong một việc lớn,
    nhắc riêng từng cái là người dùng nhận lại đúng cái danh sách họ vừa tự chia
    ra. Tiến độ của chúng đi kèm dòng việc lớn trong tin nhắc gộp.
    """
    with get_session() as session:
        stmt = (
            select(Task)
            .where(
                Task.status == TaskStatus.pending,
                Task.next_remind_at <= now,
                col(Task.parent_task_id).is_(None),
            )
            .order_by(Task.next_remind_at)
        )
        return list(session.exec(stmt).all())


def get_task(task_id: str) -> Task | None:
    with get_session() as session:
        return session.get(Task, task_id)


def upsert_task(
    task_id: str,
    group_id: uuid.UUID,
    content: str,
    due_date: date | None,
    priority: Priority,
    next_remind_at: datetime,
) -> Task:
    """R7: đồng bộ lại không tạo trùng; việc đã xong hoặc đã hủy thì giữ nguyên.

    Dòng nhóm phải tồn tại trước (khoá ngoại) — nơi gọi chạy get_or_create_group.
    content phải là bản đã gọt, xem app/core/task_text.py.

    KHÔNG ghi lại group_id: task_id băm từ chính group_id (compute_task_id), nên
    cùng một task_id thì chắc chắn cùng một nhóm — gán lại chỉ che mất bất biến
    đó. Cũng KHÔNG dời next_remind_at: tham số này chỉ dùng cho dòng tạo mới,
    nhịp nhắc là chuyện của lịch chứ không đi theo hạn, và ghi đè nó mỗi lần gửi
    lại báo cáo là đẩy lượt nhắc kế tiếp ra xa mãi.
    """
    with get_session() as session:
        existing = session.get(Task, task_id)
        if existing is not None:
            if existing.status in (TaskStatus.done, TaskStatus.cancelled):
                return existing
            # content cùng task_id vẫn có thể khác nhau ở hoa/thường và khoảng
            # trắng: compute_task_id băm bản đã gộp khoảng trắng và hạ chữ.
            existing.content = content
            existing.due_date = due_date
            existing.priority = priority
            existing.updated_at = _utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        # Mã chỉ cấp lúc tạo mới. Dòng cũ giữ nguyên mã kể cả khi tên nhóm đổi:
        # mã đã in ra cho người dùng rồi, đổi là họ gõ vào chỗ không còn ai.
        task = Task(
            task_id=task_id,
            code=allocate_code(group_id),
            group_id=group_id,
            content=content,
            due_date=due_date,
            priority=priority,
            next_remind_at=next_remind_at,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def _stamp_closed(task: Task, status: TaskStatus, now: datetime) -> None:
    """Đặt trạng thái cuối cho một dòng, kèm đúng một mốc thời gian.

    Xoá mốc của trạng thái cuối KIA luôn: ghi mỗi status là để lại một dòng tự
    mâu thuẫn — "đã hủy" nhưng vẫn còn done_at của lần báo xong trước đó, và
    hoàn tác sau này nhìn vào mốc cũ đó mà quyết định.
    """
    task.status = status
    for closed_status, field in _CLOSED_AT_FIELD.items():
        setattr(task, field, now if closed_status == status else None)
    task.updated_at = now


def _close_task(task_id: str, status: TaskStatus) -> Task | None:
    """Chuyển một việc ĐANG CHỜ sang trạng thái cuối (xong / hủy).

    Đóng một việc LỚN thì đóng luôn những việc con còn đang chờ của nó, trong
    cùng một transaction. Để lại con "đang chờ" dưới một dòng đã chốt là tiến độ
    nói một đằng ("2/4") còn trạng thái nói một nẻo ("đã xong"), và những dòng
    con đó thì không lượt quét nào chạm tới nữa nên nằm lại vĩnh viễn.

    Trả None khi dòng đã ở một trạng thái cuối KHÁC: đề xuất được ghi sau khi
    người dùng đọc bảng xác nhận, giữa hai mốc ấy dòng có thể đã bị lượt khác
    chốt rồi — lúc đó phải báo trượt chứ không ghi đè. Gật lại đúng trạng thái
    nó đang mang thì coi như xong, không ghi lần nữa.

    next_remind_at giữ nguyên: get_due_tasks chỉ quét việc pending nên mốc cũ
    không làm ai bị nhắc nhầm, còn hoàn tác thì tự đặt lại mốc.
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None:
            return None
        if task.status == status:
            return task
        if task.status != TaskStatus.pending:
            return None

        now = _utcnow()
        _stamp_closed(task, status, now)
        session.add(task)

        if task.parent_task_id is None:
            open_children = session.exec(
                select(Task).where(
                    Task.parent_task_id == task_id,
                    Task.status == TaskStatus.pending,
                )
            ).all()
            for child in open_children:
                _stamp_closed(child, status, now)
                session.add(child)

        session.commit()
        session.refresh(task)
        return task


def _reopen_task(task_id: str, closed_status: TaskStatus) -> Task | None:
    """R8: mở lại một việc đã chốt, nếu còn trong cửa sổ hoàn tác.

    Xoá CẢ HAI mốc chứ không riêng mốc của trạng thái đang mở lại: dòng quay về
    pending thì không được mang theo dấu vết done_at/cancelled_at nào, nếu không
    lượt hoàn tác sau lại thấy một mốc cũ và tưởng còn hoàn tác được.
    """
    closed_at_field = _CLOSED_AT_FIELD[closed_status]
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.status != closed_status:
            return None
        closed_at = getattr(task, closed_at_field)
        if closed_at is None or closed_at < _undo_deadline():
            return None

        now = _utcnow()
        task.status = TaskStatus.pending
        task.done_at = None
        task.cancelled_at = None
        # Nhắc lại ngay lượt quét kế tiếp: việc vừa sống lại mà mốc nhắc vẫn nằm
        # ở tương lai thì nó im lặng cho tới lúc đó.
        task.next_remind_at = now
        task.updated_at = now
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def mark_done(task_id: str) -> Task | None:
    """R8: đánh dấu xong, ghi mốc thời gian để phục vụ hoàn tác."""
    return _close_task(task_id, TaskStatus.done)


def cancel_task(task_id: str) -> Task | None:
    """Bỏ việc. Không xóa dòng: còn khôi phục được, và gửi lại báo cáo cũ không
    hồi sinh việc đã bỏ (upsert_task thấy cancelled thì để yên)."""
    return _close_task(task_id, TaskStatus.cancelled)


def undo_done(task_id: str) -> Task | None:
    """R8: chỉ cho hoàn tác nếu đánh dấu xong chưa quá cửa sổ cho phép."""
    return _reopen_task(task_id, TaskStatus.done)


def undo_cancel(task_id: str) -> Task | None:
    """R8: đối xứng undo_done, cùng cửa sổ thời gian."""
    return _reopen_task(task_id, TaskStatus.cancelled)


def set_due_date(
    task_id: str, new_due: date | None, next_remind_at: datetime
) -> Task | None:
    """Đổi hạn của một việc ĐANG CHỜ, và dời luôn mốc nhắc kế tiếp.

    Chỉ nhận dòng pending: đổi hạn một việc đã xong / đã hủy là dựng lại lịch
    nhắc cho một dòng đã đóng, và để hạn với trạng thái nói hai chuyện khác nhau.

    next_remind_at do tầng gọi tính sẵn (giống reschedule_task): tầng dữ liệu
    không biết khung giờ nhắc, đó là quy tắc nghiệp vụ ở app/core/priority.py.

    KHÔNG đụng priority: mức nâng do sắp tới hạn / quá hạn tính lúc đọc bằng
    maybe_escalate(), nên đổi hạn ở đây là mức ưu tiên hiển thị tự đi theo.
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.status != TaskStatus.pending:
            return None
        task.due_date = new_due
        task.next_remind_at = next_remind_at
        task.updated_at = _utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def reschedule_task(
    task_id: str,
    next_remind_at: datetime,
    last_reminded_at: datetime,
) -> Task | None:
    """Hẹn lượt nhắc kế tiếp. KHÔNG đụng tới priority.

    Cột priority giữ mức ưu tiên gốc của báo cáo, chỉ upsert_task ghi nó. Mức
    nâng do quá hạn / sắp tới hạn tính lúc đọc bằng maybe_escalate().
    """
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None:
            return None
        task.next_remind_at = next_remind_at
        task.last_reminded_at = last_reminded_at
        task.updated_at = _utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def find_tasks_by_reference(
    ref: str,
    limit: int = _REFERENCE_MATCH_LIMIT,
    statuses: tuple[TaskStatus, ...] = (TaskStatus.pending,),
) -> list[Task]:
    """Tìm việc theo cách người dùng nhắc tới nó: mã việc, hoặc một phần nội dung.

    Mặc định chỉ tìm trong việc đang chờ — không ai "báo xong" một việc đã xong.
    Nơi gọi nào chỉ ĐỌC (xem chi tiết, xem tiến độ) thì nới `statuses` ra, vì
    việc đã chốt vẫn xem lại được.

    Việc con cũng nằm trong tầm tìm: mã của nó ("TB-002.1") là một mã hợp lệ, và
    nội dung của nó cũng khớp được như mọi dòng khác. Gõ đúng mã thì ra tối đa
    một dòng, khỏi phải hỏi lại.
    """
    pending = select(Task).where(col(Task.status).in_(statuses))
    code = parse_code(ref)
    with get_session() as session:
        if code is not None:
            by_code = pending.where(Task.code == code).limit(limit)
            matches = list(session.exec(by_code).all())
            # Không match thì đây không phải mã việc mà chỉ TRÔNG như mã
            # ("abc123"), tìm tiếp theo nội dung thay vì báo không thấy.
            if matches:
                return matches

        keyword = _ilike_pattern(ref)
        stmt = pending.join(ProjectGroup).where(
            or_(
                col(Task.content).ilike(keyword, escape=_LIKE_ESCAPE),
                col(ProjectGroup.name).ilike(keyword, escape=_LIKE_ESCAPE),
            )
        )
        return list(session.exec(stmt.order_by(Task.due_date).limit(limit)).all())


def query_tasks(
    group: str | None = None,
    status: TaskStatus | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    limit: int = _QUERY_RESULT_LIMIT,
) -> list[Task]:
    """Lọc việc theo nhóm / trạng thái / khoảng hạn.

    KHÔNG lọc theo priority ở đây: cột priority chỉ giữ mức gốc của báo cáo, mức
    hiển thị là maybe_escalate() tính lúc đọc. Lọc bằng SQL trên cột thô sẽ bỏ
    sót đúng những việc đang được nhắc là gấp, nên bộ lọc đó nằm ở tầng tool.
    """
    with get_session() as session:
        stmt = select(Task)
        if group:
            # Khớp lỏng chứ không so tên chính xác: LLM hay truyền một phần tên
            # ("App Trưởng thôn"), so thẳng là không ra gì.
            stmt = stmt.join(ProjectGroup).where(
                col(ProjectGroup.name).ilike(_ilike_pattern(group), escape=_LIKE_ESCAPE)
            )
        if status:
            stmt = stmt.where(Task.status == status)
        if due_from:
            stmt = stmt.where(Task.due_date >= due_from)
        if due_to:
            stmt = stmt.where(Task.due_date <= due_to)
        stmt = stmt.order_by(Task.due_date).limit(limit)
        return list(session.exec(stmt).all())
