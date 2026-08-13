from datetime import date, datetime
from html import escape

from app.core.datetime_utils import to_local, weekday_vi
from app.core.reminder_digest import BUCKET_ORDER, NORMAL, OVERDUE, URGENT
from persistence.models.task import Task

# digest_text dựng HTML nên nơi gọi phải gửi kèm parse_mode này. Khai ở đây để
# hai thứ nằm cạnh nhau, đổi định dạng không quên đổi chỗ gọi.
REMINDER_PARSE_MODE = "HTML"

_BUCKET_HEADERS = {
    OVERDUE: "⛔ QUÁ HẠN",
    URGENT: "🔴 ƯU TIÊN",
    NORMAL: "🔵 BÌNH THƯỜNG",
}

_ACTION_LABELS = {
    "done": "✅ đã xong",
    "cancel": "🗑️ hủy",
}

# Thanh tiến độ. Sáu ô là đủ đọc ra tỉ lệ mà vẫn vừa một dòng trên màn hình hẹp.
_PROGRESS_BAR_WIDTH = 6
_PROGRESS_FILLED = "▰"
_PROGRESS_EMPTY = "▱"

_SUBTASK_ICONS = {"done": "✔️", "pending": "⬜", "cancelled": "🚫"}
_SUBTASK_FALLBACK_ICON = "⬜"


def _task_label(task: Task) -> str:
    """Cách gọi một việc trong tin nhắn xác nhận: mã đứng trước cho người dùng gõ lại."""
    return f"{task.code} {task.content}" if task.code else task.content


def _due_phrase(due_date: date | None, days_left: int | None) -> str:
    """Câu về hạn của một việc, dùng chung cho tin nhắc gộp và bảng chi tiết.

    Nhận sẵn `days_left` chứ không tự trừ ngày: bảng chi tiết dựng từ dict mà
    tool trả về, ở đó số ngày đã được tính một lần bằng Python (xem task_view).
    """
    if due_date is None or days_left is None:
        return "📅 Chưa có hạn"
    due_label = f"📅 Hạn {due_date.strftime('%d/%m')}"
    if days_left < 0:
        return f"{due_label} · ⏰ Quá hạn {abs(days_left)} ngày"
    if days_left == 0:
        return f"{due_label} · ⏰ Hạn chót hôm nay"
    return f"{due_label} · ⏳ Còn {days_left} ngày"


def _due_line(task: Task, now: datetime) -> str:
    if task.due_date is None:
        return _due_phrase(None, None)
    days_left = (task.due_date - to_local(now).date()).days
    return _due_phrase(task.due_date, days_left)


def progress_line(done: int, total: int) -> str:
    """"Tiến độ: 2/4 (50%) ▰▰▰▱▱▱".

    Tổng bằng 0 thì thanh rỗng và 0% — không chia cho 0, và cũng không nói dối
    là 100% khi chưa có đầu mục nào.
    """
    filled = round(_PROGRESS_BAR_WIDTH * done / total) if total else 0
    bar = _PROGRESS_FILLED * filled + _PROGRESS_EMPTY * (_PROGRESS_BAR_WIDTH - filled)
    percent = round(100 * done / total) if total else 0
    return f"Tiến độ: {done}/{total} ({percent}%) {bar}"


def digest_text(
    buckets: dict[str, list[Task]],
    now: datetime,
    progress: dict[str, tuple[int, int]] | None = None,
) -> str:
    """Một tin duy nhất cho cả lượt nhắc, chia rổ theo mức ưu tiên.

    `progress` là {task_id: (đã xong, tổng)} của những việc đã chia nhỏ; việc
    nào không có khoá trong đó thì không in dòng tiến độ. Truyền từ ngoài vào
    chứ không đọc `task.subtasks`: hàm này thuần, và Job A đã đọc cả lô một lượt.

    Tên nhóm và content là dữ liệu người dùng nhập nên phải escape: một dấu '<'
    trong tên nhóm là đủ để Telegram từ chối cả tin nhắn.
    """
    total = sum(len(tasks) for tasks in buckets.values())
    lines = [f"🔔 <b>Bạn có {total} việc cần làm</b>"]
    progress = progress or {}

    for bucket in BUCKET_ORDER:
        tasks = buckets.get(bucket) or []
        if not tasks:
            continue
        lines.append(f"\n<b>{_BUCKET_HEADERS[bucket]} ({len(tasks)})</b>")
        for task in tasks:
            code = f"<code>{escape(task.code)}</code> · " if task.code else ""
            group = escape(task.group_name, quote=False)
            content = escape(task.content, quote=False)
            lines.append(f"{code}{content}\n    📁 {group} · {_due_line(task, now)}")
            counts = progress.get(task.task_id)
            if counts:
                lines.append(f"    📊 {progress_line(*counts)}")

    return "\n".join(lines)


def task_detail_text(view: dict) -> str:
    """Bảng chi tiết một đầu việc: hạn, tiến độ, và danh sách việc con.

    Dựng từ dict do tool `get_task_detail` trả về — cùng lý do với
    update_confirm_text: giá trị gốc của tool mới là bản chuẩn, để LLM kể lại
    thì nó sắp xếp lại số liệu theo ý nó.

    Text thuần chứ không HTML: nội dung việc đi thẳng vào đây, escape sót một
    dấu '<' là mất cả tin nhắn.
    """
    task = view.get("task") or {}
    subtasks = view.get("subtasks") or []
    counts = view.get("progress") or {}

    code = task.get("code") or ""
    header = f"📌 {code} · {task.get('content') or ''}".strip()
    due = _due_phrase(_parse_date(task.get("due_date")), task.get("days_left"))
    lines = [f"{header}\n{due}"]

    if not subtasks:
        lines.append("\nChưa chia việc con nào. Nhắn cho tôi biết cần thêm đầu mục gì.")
        return "\n".join(lines)

    lines.append(progress_line(counts.get("done", 0), counts.get("total", 0)))
    for subtask in subtasks:
        icon = _SUBTASK_ICONS.get(subtask.get("status"), _SUBTASK_FALLBACK_ICON)
        sub_code = subtask.get("code") or ""
        lines.append(f"{icon} {sub_code} {subtask.get('content') or ''}".strip())
    return "\n".join(lines)


def update_confirm_text(pending_updates: list[dict]) -> str:
    """Bảng đề xuất cập nhật, chờ người dùng nhắn duyệt.

    Text thuần chứ không HTML: nội dung việc đi thẳng vào đây, escape sót một
    dấu '<' là mất cả tin nhắn.
    """
    lines = ["Tôi sẽ cập nhật:"]
    for update in pending_updates:
        code = update.get("code") or ""
        content = update.get("content") or ""
        lines.append(f"• {code} {content} → {_change_label(update)}".strip())
    lines.append('\nNhắn "ok" để tôi ghi, hoặc "thôi" để bỏ qua.')
    return "\n".join(lines)


def _change_label(update: dict) -> str:
    """Một dòng đề xuất đọc lên thành gì.

    Ba hành động động tới việc con in luôn nội dung mới: "thêm việc con" mà
    không nói thêm cái gì thì người dùng gật vào một thứ họ chưa đọc.
    """
    action = update.get("action")
    if action == "reschedule":
        return f"📅 hạn mới {_due_label(update.get('new_due_date'))}"
    if action == "add_subtask":
        return f'➕ thêm việc con "{update.get("subtask_content") or ""}"'
    if action == "rename_subtask":
        return f'✏️ sửa thành "{update.get("subtask_content") or ""}"'
    return _ACTION_LABELS.get(action, action or "")


def _parse_date(iso_date: str | None) -> date | None:
    """Ngày ISO trong dict của tool -> date. Hỏng thì coi như không có hạn."""
    try:
        return date.fromisoformat(iso_date) if iso_date else None
    except ValueError:
        return None


def _due_label(iso_date: str | None) -> str:
    """Ngày ISO đọc lên theo kiểu người Việt, kèm thứ để dễ soát."""
    if not iso_date:
        return "chưa rõ"
    day = date.fromisoformat(iso_date)
    return f"{day.strftime('%d/%m/%Y')} ({weekday_vi(day)})"


def done_confirmation_text(task: Task) -> str:
    return f"✅ Đã xong: {_task_label(task)}"


def cancel_confirmation_text(task: Task) -> str:
    return f"🗑️ Đã hủy: {_task_label(task)}"


def undo_confirmation_text(task: Task) -> str:
    return f"↩️ Đã hoàn tác, việc quay lại trạng thái chưa xong: {_task_label(task)}"


def subtask_added_text(task: Task) -> str:
    return f"➕ Đã thêm việc con: {_task_label(task)}"


def subtask_renamed_text(task: Task) -> str:
    return f"✏️ Đã sửa việc con: {_task_label(task)}"


def due_updated_text(task: Task) -> str:
    new_due = task.due_date.isoformat() if task.due_date else None
    return f"📅 Đã đổi hạn: {_task_label(task)} → {_due_label(new_due)}"


def update_failed_text() -> str:
    return "Không cập nhật được, việc này không còn trong danh sách nữa."


def missing_due_date_prompt(task: Task) -> str:
    return f"❓ Việc này chưa có hạn: {_task_label(task)}\nBạn cho biết hạn là ngày nào?"


def extraction_summary_text(tasks: list[dict]) -> str:
    if not tasks:
        # Không nhắc riêng khối "Tiếp theo:" nữa: cùng nhánh này giờ nhận cả lời
        # thoại và chữ đọc từ ảnh, ở đó không có khối nào để mà thiếu.
        return "Tôi không thấy đầu việc nào trong nội dung này."
    lines = [f"Trích được {len(tasks)} đầu việc:"]
    for order, task in enumerate(tasks, 1):
        priority_icon = "🔴" if task.get("priority") == "urgent" else "🔵"
        due_label = task.get("due_date") or "chưa có hạn"
        lines.append(
            f"{order}. {priority_icon} [{task.get('group')}] "
            f"{task.get('content')} — {due_label}"
        )
    lines.append("\nNhắn 'ok' để lưu, hoặc nói rõ cần sửa chỗ nào.")
    return "\n".join(lines)


def saved_text(count: int) -> str:
    return f"💾 Đã lưu {count} đầu việc. Bot sẽ bắt đầu nhắc theo nhịp đã cấu hình."


def abandoned_text() -> str:
    return "Đã bỏ qua, không lưu gì cả."


_MEDIA_UNDERSTOOD_HEADERS = {
    "photo": "🖼️ Tôi đọc được từ ảnh:",
    "voice": "🎙️ Tôi nghe được:",
}
_MEDIA_FAILED_TEXTS = {
    "photo": "Tôi không đọc được nội dung trong ảnh này. Bạn gõ lại giúp tôi nhé.",
    "voice": "Tôi không nghe rõ tin nhắn thoại này. Bạn nói lại hoặc gõ giúp tôi nhé.",
}


def media_understood_text(kind: str, text: str) -> str:
    """Trả lại cho người dùng thứ bot vừa đọc/nghe được, trước khi hành động.

    Nghe nhầm một cái tên hay một con số thì họ phát hiện ngay ở đây, chứ không
    phải đợi tới lúc bảng đầu việc hiện ra với nội dung lạ.
    """
    header = _MEDIA_UNDERSTOOD_HEADERS.get(kind, "Tôi hiểu nội dung là:")
    return f"{header}\n{text}"


def media_failed_text(kind: str) -> str:
    return _MEDIA_FAILED_TEXTS.get(
        kind, "Tôi không xử lý được file này. Bạn gõ lại giúp tôi nhé."
    )


def no_answer_text() -> str:
    return "Tôi chưa trả lời được câu này. Bạn hỏi lại rõ hơn giúp tôi nhé."


def need_reason_text() -> str:
    return "Bạn cho biết cần sửa chỗ nào để tôi trích lại (ví dụ: 'bỏ việc số 3')."
