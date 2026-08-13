"""Việc con: chia một đầu việc lớn thành danh sách đầu mục, và đếm tiến độ.

Việc con VẪN là một dòng `task`, chỉ khác đúng một chỗ: `parent_task_id` trỏ về
việc lớn. Nhờ vậy mọi thứ đã có — báo xong, hủy, đổi hạn, tra theo mã, hoàn tác
— chạy nguyên xi trên việc con mà không phải viết nhánh thứ hai; file này chỉ
cần lo ba việc mà bảng phẳng không tự làm được: gắn con vào cha, cấp mã ".N", và
đếm tiến độ.

CHỈ MỘT TẦNG. `add_subtask` từ chối gắn con vào một dòng đã có cha: cây sâu hơn
một tầng thì tin nhắn Telegram không hiển thị nổi, mà tiến độ cũng hết nghĩa —
"2/4" của một nút giữa cây nói về cái gì thì tuỳ người đọc.
"""

import uuid
from datetime import date, datetime, timezone

from sqlmodel import Session, col, select

from app.core.task_code import format_subtask_code
from app.core.task_text import normalize_content
from persistence.models.task import Task, TaskStatus
from persistence.pool import get_session

# task_id của việc lớn là băm (nhóm + nội dung) để R7 đồng bộ lại không tạo
# trùng. Việc con KHÔNG băm được như vậy: đổi tên một việc con là chuyện thường
# (`rename_subtask`), mà băm nội dung thì đổi tên xong id cũ trỏ vào một nội
# dung không còn tồn tại — lần sau thêm lại đúng cái tên cũ sẽ đụng vào chính
# dòng vừa bị đổi tên. Chống trùng ở đây làm bằng cách tra nội dung dưới cùng
# một cha, xem _matching_open_subtask.
_SUBTASK_ID_LENGTH = 16


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_subtask_id() -> str:
    return uuid.uuid4().hex[:_SUBTASK_ID_LENGTH]


def _content_key(content: str) -> str:
    """Khoá so trùng nội dung việc con: bản đã gọt, gộp khoảng trắng, hạ chữ."""
    return " ".join(normalize_content(content).lower().split())


def _matching_open_subtask(
    session: Session, parent_task_id: str, content: str
) -> Task | None:
    """Việc con đang chờ có cùng nội dung dưới cùng một việc lớn, nếu có.

    So bằng Python chứ không bằng ILIKE: một đầu việc chỉ có vài việc con, mà so
    trong SQL thì lại phải escape `%`/`_` của người dùng thêm một lần nữa.
    """
    wanted = _content_key(content)
    stmt = select(Task).where(
        Task.parent_task_id == parent_task_id,
        Task.status == TaskStatus.pending,
    )
    for task in session.exec(stmt).all():
        if _content_key(task.content) == wanted:
            return task
    return None


def _take_next_sub_seq(session: Session, parent_task_id: str) -> int | None:
    """Số thứ tự việc con kế tiếp của một việc lớn, và khoá dòng cha lại.

    Chỉ SELECT một cột chứ không select cả entity: `Task` nạp sẵn `project_group`
    bằng LEFT OUTER JOIN (lazy="joined"), mà Postgres từ chối `FOR UPDATE` trên
    câu có nhánh ngoài nullable. Lấy đúng cột cần thì câu lệnh không còn join.

    Khoá giữ tới hết transaction, nên câu đọc dòng cha ngay sau đó thấy giá trị
    này và không lượt nào khác chen vào cấp trùng mã.
    """
    return session.exec(
        select(Task.next_sub_seq)
        .where(Task.task_id == parent_task_id)
        .with_for_update()
    ).first()


def add_subtask(
    parent_task_id: str, content: str, due_date: date | None = None
) -> Task | None:
    """Thêm một việc con vào việc lớn. Trả None nếu không gắn được.

    Không gắn được có ba lý do, và cả ba đều KHÔNG ghi gì: cha không tồn tại,
    cha đã chốt (xong/hủy) nên thêm việc vào là dựng lại một dòng đã đóng, hoặc
    cha chính nó đã là việc con (chỉ một tầng).

    Thêm lại đúng một nội dung đang có thì trả về chính dòng cũ chứ không tạo
    dòng thứ hai, và cũng không tiêu một số thứ tự — LLM đề xuất trùng là chuyện
    xảy ra thật, người dùng gật hai lần không được đẻ ra hai đầu mục giống nhau.

    Việc con thừa hưởng nhóm, mức ưu tiên và hạn của cha khi không nói gì khác:
    nó là một phần của cùng công việc đó. `next_remind_at` cũng chép theo cha cho
    đủ ràng buộc NOT NULL — việc con không nằm trong lượt quét nhắc nên giá trị
    này không dẫn tới tin nhắn nào.
    """
    clean_content = normalize_content(content)
    if not clean_content:
        return None

    with get_session() as session:
        next_seq = _take_next_sub_seq(session, parent_task_id)
        if next_seq is None:
            return None
        parent = session.get(Task, parent_task_id)
        if parent is None or parent.status != TaskStatus.pending:
            return None
        if parent.parent_task_id is not None:
            return None

        existing = _matching_open_subtask(session, parent_task_id, clean_content)
        if existing is not None:
            return existing

        subtask = Task(
            task_id=_new_subtask_id(),
            # Cha chưa có mã thì con cũng chịu: mã con dựng từ mã cha, không có
            # nguồn nào khác để suy ra. Dòng vẫn tra được theo nội dung.
            code=format_subtask_code(parent.code, next_seq) if parent.code else None,
            group_id=parent.group_id,
            parent_task_id=parent.task_id,
            content=clean_content,
            due_date=due_date if due_date is not None else parent.due_date,
            priority=parent.priority,
            next_remind_at=parent.next_remind_at,
        )
        parent.next_sub_seq = next_seq + 1
        parent.updated_at = _utcnow()
        session.add(subtask)
        session.add(parent)
        session.commit()
        session.refresh(subtask)
        return subtask


def rename_subtask(task_id: str, content: str) -> Task | None:
    """Sửa nội dung một việc con ĐANG CHỜ. Mã của nó giữ nguyên.

    Chỉ nhận dòng đang chờ và phải đúng là việc con: đổi tên một việc lớn là
    chuyện của báo cáo (upsert_task ghi theo task_id băm từ nội dung), làm ở đây
    sẽ để tên hiển thị lệch khỏi cái băm sinh ra id.
    """
    clean_content = normalize_content(content)
    if not clean_content:
        return None

    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None or task.parent_task_id is None:
            return None
        if task.status != TaskStatus.pending:
            return None
        task.content = clean_content
        task.updated_at = _utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def get_subtasks(parent_task_id: str) -> list[Task]:
    """Danh sách việc con còn hiệu lực của một việc lớn, theo thứ tự được thêm.

    Bỏ việc con đã hủy: với người dùng, "xóa việc con" chính là hủy nó, nên nó
    phải biến mất khỏi danh sách chứ không nằm đó với dấu gạch ngang.

    Sắp theo created_at chứ không theo mã: "TB-002.10" đứng trước "TB-002.2" nếu
    so chuỗi, mà mã con không đệm số 0 để so cho đúng.
    """
    with get_session() as session:
        stmt = (
            select(Task)
            .where(
                Task.parent_task_id == parent_task_id,
                Task.status != TaskStatus.cancelled,
            )
            .order_by(Task.created_at)
        )
        return list(session.exec(stmt).all())


def subtask_progress(parent_task_ids: list[str]) -> dict[str, tuple[int, int]]:
    """{task_id việc lớn: (số con đã xong, tổng số con)} cho cả lô.

    Nhận cả lô vì tin nhắc gộp cần tiến độ của mọi việc trong lượt quét: hỏi
    từng cái là một truy vấn cho mỗi dòng.

    Việc lớn không có con thì KHÔNG có khoá trong kết quả — nơi gọi phân biệt
    được "chưa chia nhỏ" với "chia rồi mà chưa xong cái nào" (0, n).

    Đếm bằng Python thay vì count(*) FILTER: mỗi đầu việc chỉ vài con, mà viết
    tay câu gộp là buộc chặt vào phương ngữ SQL của Postgres.
    """
    if not parent_task_ids:
        return {}

    progress: dict[str, tuple[int, int]] = {}
    with get_session() as session:
        stmt = select(Task).where(
            col(Task.parent_task_id).in_(parent_task_ids),
            Task.status != TaskStatus.cancelled,
        )
        for task in session.exec(stmt).all():
            done, total = progress.get(task.parent_task_id, (0, 0))
            is_done = task.status == TaskStatus.done
            progress[task.parent_task_id] = (done + int(is_done), total + 1)
    return progress
