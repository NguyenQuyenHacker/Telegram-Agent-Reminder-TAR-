from datetime import datetime
from html import escape

from app.core.datetime_utils import to_local
from app.core.task_text import normalize_content
from persistence.models.task import Priority, Task

# reminder_text dựng HTML nên nơi gọi phải gửi kèm parse_mode này. Khai ở đây
# để hai thứ nằm cạnh nhau, đổi định dạng không quên đổi chỗ gọi.
REMINDER_PARSE_MODE = "HTML"


def _display_content(task: Task) -> str:
    """Nội dung đã gọt phần trùng lặp với các dòng khác của tin nhắn.

    "ƯU TIÊN:" đã có badge nói hộ, "(hạn 20/8)" đã có dòng 📅 nói hộ.
    """
    return normalize_content(task.content)


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


def reminder_text(task: Task, now: datetime, priority: Priority | None = None) -> str:
    """Tin nhắn nhắc việc, định dạng HTML.

    group và content là dữ liệu người dùng nhập nên phải escape: một dấu '<'
    trong tên nhóm là đủ để Telegram từ chối cả tin nhắn.
    """
    effective_priority = priority or task.priority
    group = escape(task.group, quote=False)
    content = escape(_display_content(task), quote=False)

    # Việc gấp: nhãn tách hẳn ra dòng riêng cho đập vào mắt. Việc thường không
    # cần dòng đó, chấm màu đứng luôn trước tên nhóm cho gọn.
    if effective_priority == Priority.urgent:
        header = f"🔴 <b>ƯU TIÊN</b>\n📁 {group}"
    else:
        header = f"🔵 {group}"

    return f"{header}\n\n<b>{content}</b>\n{_due_line(task, now)}"


def done_confirmation_text(task: Task) -> str:
    return f"✅ Đã xong: {task.content}"


def snooze_confirmation_text(task: Task, next_remind_at: datetime) -> str:
    next_remind_label = to_local(next_remind_at).strftime("%H:%M %d/%m")
    return f"⏰ Đã hoãn: {task.content}\nSẽ nhắc lại lúc {next_remind_label}"


def undo_confirmation_text(task: Task) -> str:
    return f"↩️ Đã hoàn tác, việc quay lại trạng thái chưa xong: {task.content}"


def undo_expired_text() -> str:
    return "Quá 24 giờ nên không hoàn tác được nữa."


def missing_due_date_prompt(task: Task) -> str:
    return f"❓ Việc này chưa có hạn: {task.content}\nBạn cho biết hạn là ngày nào?"


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
