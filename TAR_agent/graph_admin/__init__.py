"""Graph của bot admin: nạp và quản lý kho tri thức.

              ┌─ ask_project → check_file → parse → store → report ─┐
    START → route                                                    ├─→ END
              └─ handle_text ────────────────────────────────────────┘

Nhánh nạp tuyến tính, không node nào gọi LLM. Nhánh chữ hiện là ba lệnh cố định
— chỗ dành sẵn cho vòng ReAct quản lý kho.

    nodes/    chạm state: đọc state -> gọi helpers -> ghi state
    helpers/  hàm thuần, không biết state là gì
    tools/    vỏ @tool cho LLM gọi (rỗng tới khi dựng ReAct)

Không import aiogram ở đâu trong package này: node append event vào `outbox`,
tầng Telegram mới render và gửi.
"""
