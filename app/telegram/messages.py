from datetime import datetime

from app.core.datetime_utils import to_local
from persistence.models.task import Priority, Task


def _due_line(task: Task, now: datetime) -> str:
    if task.due_date is None:
        return "📅 Chưa có hạn"
    today = to_local(now).date()
    delta = (task.due_date - today).days
    stamp = task.due_date.strftime("%d/%m")
    if delta < 0:
        return f"⏰ Quá hạn {abs(delta)} ngày ({stamp})"
    if delta == 0:
        return f"⏰ Hạn chót hôm nay ({stamp})"
    return f"⏰ Còn {delta} ngày ({stamp})"


def reminder_text(task: Task, now: datetime, priority: Priority | None = None) -> str:
    effective = priority or task.priority
    badge = "🔴 ƯU TIÊN" if effective == Priority.urgent else "🔵"
    content = task.content.removeprefix("ƯU TIÊN:").strip()
    return f"{badge} · {task.group}\n{content}\n{_due_line(task, now)}"


def done_confirmation_text(task: Task) -> str:
    return f"✅ Đã xong: {task.content}"


def snooze_confirmation_text(task: Task, next_remind_at: datetime) -> str:
    stamp = to_local(next_remind_at).strftime("%H:%M %d/%m")
    return f"⏰ Đã hoãn: {task.content}\nSẽ nhắc lại lúc {stamp}"


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
    for i, t in enumerate(tasks, 1):
        flag = "🔴" if t.get("priority") == "urgent" else "🔵"
        due = t.get("due_date") or "chưa có hạn"
        lines.append(f"{i}. {flag} [{t.get('group')}] {t.get('content')} — {due}")
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
