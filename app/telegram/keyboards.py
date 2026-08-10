from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def reminder_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Đã xong", callback_data=f"task:done:{task_id}"),
                InlineKeyboardButton(text="⏰ Nhắc sau", callback_data=f"task:snooze:{task_id}"),
            ]
        ]
    )


def undo_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Hoàn tác", callback_data=f"task:undo:{task_id}")]
        ]
    )

# Bảng đầu việc KHÔNG có nút: người dùng trả lời bằng tin nhắn thường, LLM đọc ý
# định (xem reminder_agent/utils/intent.py). Nút inline không tự mất sau khi bấm
# nên dễ bấm lại nhầm vào phiên đã xong.
