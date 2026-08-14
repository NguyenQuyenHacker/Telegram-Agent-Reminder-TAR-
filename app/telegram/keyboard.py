"""Bàn phím inline cho hai điểm dừng của luồng nạp: choose_project, confirm_overwrite.

`callback_data` mang THẲNG giá trị resume, không mã hoá thêm gì về routing —
resume luôn đi vào đúng thread_id của chat đang bấm (xem webhooks.thread_config,
khoá theo chat chứ không theo message), nên callback không cần tự nêu "đây là
lượt nạp nào".
"""

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_PROJECT_PREFIX = "p:"
_OVERWRITE_YES = "ow:1"
_OVERWRITE_NO = "ow:0"


def choose_project_keyboard(projects: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{p['name']} ({p['document_count']})",
                callback_data=_PROJECT_PREFIX + str(p["project_id"]),
            )
        ]
        for p in projects
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_overwrite_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Ghi đè", callback_data=_OVERWRITE_YES),
                InlineKeyboardButton(text="Huỷ", callback_data=_OVERWRITE_NO),
            ]
        ]
    )


def resume_value(callback_data: str) -> dict[str, Any]:
    """callback_data -> payload cho Command(resume=...).

    Ném ValueError nếu không nhận ra — nơi gọi phải coi đó là bấm vào nút cũ
    (bàn phím của một lượt nạp đã xong từ trước), không phải lỗi hệ thống.
    """
    if callback_data.startswith(_PROJECT_PREFIX):
        return {"project_id": callback_data[len(_PROJECT_PREFIX) :]}
    if callback_data == _OVERWRITE_YES:
        return {"overwrite": True}
    if callback_data == _OVERWRITE_NO:
        return {"overwrite": False}
    raise ValueError(f"callback_data lạ: {callback_data}")
