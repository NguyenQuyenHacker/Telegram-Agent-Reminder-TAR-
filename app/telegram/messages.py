from datetime import date, datetime
from html import escape

from app.core.datetime_utils import to_local, weekday_vi
from app.core.reminder_digest import BUCKET_ORDER, NORMAL, OVERDUE, URGENT
from app.core.task_text import normalize_content
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


def _display_content(task: Task) -> str:
    """Nội dung đã gọt phần trùng lặp với các dòng khác của tin nhắn.

    "ƯU TIÊN:" đã có rổ nói hộ, "(hạn 20/8)" đã có dòng 📅 nói hộ.
    """
    return normalize_content(task.content)


def _task_label(task: Task) -> str:
    """Cách gọi một việc trong tin nhắn xác nhận: mã đứng trước cho người dùng gõ lại."""
    content = _display_content(task)
    return f"{task.code} {content}" if task.code else content


def _due_line(task: Task, now: datetime) -> str:
    if task.due_date is None:
        return "📅 Chưa có hạn"
    today = to_local(now).date()
    days_left = (task.due_date - today).days
    due_label = f"📅 Hạn {task.due_date.strftime('%d/%m')}"
    if days_left < 0:
        return f"{due_label} · ⏰ Quá hạn {abs(days_left)} ngày"
    if days_left == 0:
        return f"{due_label} · ⏰ Hạn chót hôm nay"
    return f"{due_label} · ⏳ Còn {days_left} ngày"


def digest_text(buckets: dict[str, list[Task]], now: datetime) -> str:
    """Một tin duy nhất cho cả lượt nhắc, chia rổ theo mức ưu tiên.

    group và content là dữ liệu người dùng nhập nên phải escape: một dấu '<'
    trong tên nhóm là đủ để Telegram từ chối cả tin nhắn.
    """
    total = sum(len(tasks) for tasks in buckets.values())
    lines = [f"🔔 <b>Bạn có {total} việc cần làm</b>"]

    for bucket in BUCKET_ORDER:
        tasks = buckets.get(bucket) or []
        if not tasks:
            continue
        lines.append(f"\n<b>{_BUCKET_HEADERS[bucket]} ({len(tasks)})</b>")
        for task in tasks:
            code = f"<code>{escape(task.code)}</code> · " if task.code else ""
            group = escape(task.group, quote=False)
            content = escape(_display_content(task), quote=False)
            lines.append(f"{code}{content}\n    📁 {group} · {_due_line(task, now)}")

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
        action = update.get("action")
        if action == "reschedule":
            change = f"📅 hạn mới {_due_label(update.get('new_due_date'))}"
        else:
            change = _ACTION_LABELS.get(action, action or "")
        lines.append(f"• {code} {content} → {change}".strip())
    lines.append('\nNhắn "ok" để tôi ghi, hoặc "thôi" để bỏ qua.')
    return "\n".join(lines)


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


def due_updated_text(task: Task) -> str:
    new_due = task.due_date.isoformat() if task.due_date else None
    return f"📅 Đã đổi hạn: {_task_label(task)} → {_due_label(new_due)}"


def update_failed_text() -> str:
    return "Không cập nhật được, việc này không còn trong danh sách nữa."


def missing_due_date_prompt(task: Task) -> str:
    return f"❓ Việc này chưa có hạn: {_task_label(task)}\nBạn cho biết hạn là ngày nào?"


def extraction_summary_text(tasks: list[dict]) -> str:
    if not tasks:
        return "Không trích được đầu việc nào ở khối 'Tiếp theo:'."
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


def no_answer_text() -> str:
    return "Tôi chưa trả lời được câu này. Bạn hỏi lại rõ hơn giúp tôi nhé."


def need_reason_text() -> str:
    return "Bạn cho biết cần sửa chỗ nào để tôi trích lại (ví dụ: 'bỏ việc số 3')."
