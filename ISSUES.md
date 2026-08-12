# ISSUES

Những thứ phát hiện trong lượt rà soát nhưng **KHÔNG được sửa** theo yêu cầu:
logic nghiệp vụ, dead code, bug, race condition, và file test/config.
Ghi lại để quyết định sau, kèm file + số dòng (theo cây mã sau khi refactor).

---

## 1. Dead code — ghi ra, KHÔNG xoá

| # | Vị trí | Mô tả |
|---|--------|-------|
| 1.1 | [app/telegram/messages.py:111](app/telegram/messages.py#L111) | `undo_confirmation_text()` không có nơi nào gọi. |
| 1.2 | [persistence/proc/tasks.py:101](persistence/proc/tasks.py#L101) | `undo_done()` không có nơi nào gọi. |
| 1.3 | [persistence/proc/tasks.py:137](persistence/proc/tasks.py#L137) | `undo_cancel()` không có nơi nào gọi. |
| 1.4 | [app/telegram/sender.py:47](app/telegram/sender.py#L47) | `edit_message()` không có nơi nào gọi — hệ thống đã bỏ hết nút inline, mọi thao tác giờ là tin nhắn thường. |
| 1.5 | [app/telegram/bot.py:27](app/telegram/bot.py#L27) | `delete_webhook()` không có nơi nào gọi. Dễ nhầm: [app/telegram/polling.py:36](app/telegram/polling.py#L36) gọi `bot.delete_webhook(...)` — đó là method của aiogram, không phải hàm này. |
| 1.6 | [persistence/proc/tasks.py:23](persistence/proc/tasks.py#L23) | `_undo_deadline()` chỉ phục vụ 1.2 và 1.3, nên cũng chết theo. |
| 1.7 | [TEST/test_bot.py](TEST/test_bot.py) | Cả file: bot thử nghiệm đứng riêng (đọc ảnh, gọi Gemini), không nằm trong app và không thuộc bộ test `tests/`. Nó `import langchain.chat_models` — gói `langchain` KHÔNG có trong requirements.txt, nên file này không chạy được bằng môi trường chuẩn của dự án. |

> **Cập nhật (lượt rà soát đồng bộ trường):** 1.2 + 1.3 vẫn chưa có nơi gọi,
> nhưng hai hàm giờ dùng chung `_reopen_task()` và đã xoá cả `done_at` lẫn
> `cancelled_at` khi mở lại — xem [CHANGES.md](CHANGES.md).

**Nhận xét chung về nhóm này:** 1.1–1.3 + 1.6 là toàn bộ đường hoàn tác của
R8 (`undo_window_hours`, `Task.done_at`, `Task.cancelled_at` phần khôi phục).
Dữ liệu vẫn được ghi đúng, nhưng **không có tool nào của agent gọi tới**, nên
người dùng hiện không có cách nào hoàn tác. Đây là tính năng chưa nối dây chứ
không hẳn là rác — cần quyết định: nối vào agent, hay bỏ hẳn cả cụm.

---

## 2. Bug / race condition — ghi ra, KHÔNG sửa

| # | Vị trí | Mô tả |
|---|--------|-------|
| 2.1 | [app/routers/webhooks.py:41](app/routers/webhooks.py#L41) | **Race**: `_is_awaiting_confirm()` đọc state rồi mới `ainvoke()`. Hai tin nhắn tới sát nhau (hoặc cùng chat trên 2 worker) có thể cùng đọc ra "chưa chờ duyệt" và cùng mở lượt mới, hoặc cùng resume một interrupt. Kiểm tra và ghi không nằm trong một transaction. |
| 2.2 | [persistence/proc/tasks.py:72](persistence/proc/tasks.py#L72) | **Session lồng nhau**: `upsert_task()` đang mở session thì gọi `allocate_code(group)`, hàm này mở session THỨ HAI và chạy `SELECT ... FOR UPDATE` ([persistence/proc/group_codes.py:29](persistence/proc/group_codes.py#L29)). Hai kết nối cùng lúc từ một luồng — dưới tải song song có nguy cơ deadlock, và nếu `upsert_task` rollback thì mã việc đã cấp vẫn bị tiêu (số nhảy cóc). |
| 2.3 | [persistence/proc/group_codes.py:46](persistence/proc/group_codes.py#L46) | Chỉ thử lại `IntegrityError` **một lần**. Lần đụng thứ hai sẽ ném lỗi ra ngoài. Xác suất thấp ở quy mô hiện tại nhưng không có gì chặn. |
| 2.4 | [persistence/proc/tasks.py:218](persistence/proc/tasks.py#L218) | `keyword = f"%{ref.strip()}%"` — **không** phải lỗ hổng SQL injection (đi qua tham số bound của `ilike`), nhưng `%` và `_` người dùng gõ vào vẫn được hiểu là ký tự đại diện. Gõ "100%" là quét cả bảng. Thiếu escape LIKE. |
| 2.5 | [app/services/reminder_service.py:75](app/services/reminder_service.py#L75) | Gửi tin gộp xong mới `_reschedule_after_digest` từng việc. Tiến trình chết giữa vòng lặp thì phần việc chưa kịp dời mốc sẽ bị nhắc lại ở lượt quét sau. Không mất dữ liệu, chỉ phiền. |
| 2.6 | [reminder_agent/graph.py:398](reminder_agent/graph.py#L398) | `call_tools` đọc thẳng `state["messages"][-1].tool_calls`. Đúng khi tới từ `route_agent`, nhưng node không tự bảo vệ nếu sau này có cạnh khác trỏ vào. |
| 2.7 | [reminder_agent/utils/tools/update_task.py:47](reminder_agent/utils/tools/update_task.py#L47) | `_parse_due()` nuốt `ValueError` và trả `None`. Với `action="reschedule"` thì bắt được (trả `invalid_due_date`), nhưng với `done`/`cancel` kèm ngày sai định dạng thì ngày hỏng bị bỏ im lặng. |
| 2.8 | [app/core/config.py:39](app/core/config.py#L39) | `settings = Settings()` chạy lúc import. Thiếu một biến môi trường bắt buộc là **mọi** file import trực tiếp/gián tiếp đều chết ngay khi import, kể cả code không đụng gì tới cấu hình. Làm test khó chạy độc lập. |

> **Cập nhật (lượt rà soát đồng bộ trường):** 2.4 và 2.7 **đã sửa** — mẫu ILIKE
> giờ escape `%`/`_`, và ngày gửi kèm `done`/`cancel` bị bỏ ngay tại tool thay vì
> nuốt im lặng. 2.1, 2.2, 2.3, 2.5, 2.6, 2.8 vẫn còn nguyên.

---

## 3. Cấu hình / môi trường — không đụng theo yêu cầu

| # | Vị trí | Mô tả |
|---|--------|-------|
| 3.1 | [requirements.txt](requirements.txt) | **Thiếu `tzdata`.** `app/core/datetime_utils.py:6` gọi `ZoneInfo("Asia/Ho_Chi_Minh")`; Windows và image Docker slim không có sẵn cơ sở dữ liệu múi giờ IANA → `ZoneInfoNotFoundError` ngay lúc import. Gặp đúng lỗi này khi dựng môi trường chạy test cho lượt rà soát này. |
| 3.2 | [requirements.txt](requirements.txt) | `pyyaml==6.0.2` không build được trên Python 3.14 (không có wheel, build from source lỗi). Cần nâng pin nếu định chạy trên 3.14. |
| 3.3 | [TEST/test_bot.py:13](TEST/test_bot.py#L13) | `os.environ["BOT_TOKEN"]` — `KeyError` ngay lúc import nếu chưa có biến. Nếu `pytest` được trỏ vào thư mục này thì cả lượt chạy hỏng. |

**Không tìm thấy secret hardcode.** `.env` đã nằm trong `.gitignore`, và quét
toàn bộ `*.py` theo mẫu `api_key/token/secret/password = "..."` không ra kết quả.
`_DELETE_SQL` trong [persistence/proc/checkpoints.py](persistence/proc/checkpoints.py)
là SQL thô nhưng dùng tham số bound `:cutoff_uuid`, không nối chuỗi.

---

## 4. Ghi chú về logic nghiệp vụ (chỉ nêu, không sửa)

| # | Vị trí | Mô tả |
|---|--------|-------|
| 4.1 | [reminder_agent/graph.py:107](reminder_agent/graph.py#L107) | `_apply_reschedule` đặt mốc nhắc kế tiếp bằng `compute_next_remind_at(now_local())` — tính từ **bây giờ**, không phải từ hạn mới. **Đã xác nhận là chủ ý** và sửa lại comment cho đúng: nhịp nhắc là hằng số cấu hình, không phái sinh từ hạn. |
| 4.3 | [reminder_agent/utils/tools/update_task.py](reminder_agent/utils/tools/update_task.py) | `propose_task_update(action="reschedule")` **nhận cả hạn trong quá khứ**. Cố ý không chặn — dời hạn về trước là thao tác hợp lệ — nhưng cũng là nơi một lỗi đoán năm của LLM ("20/7" ra 2025) đi lọt. Bảng xác nhận có in ngày kèm thứ nên người dùng còn cơ hội soát. |
| 4.2 | [persistence/proc/reports.py:19](persistence/proc/reports.py#L19) | `insert_report` khử trùng theo `source_hash`: gửi lại y hệt một báo cáo sẽ trả về bản ghi cũ chứ không tạo bản mới. Đúng ý R7 nhưng nghĩa là không lưu được lịch sử "đã gửi lại lúc nào". |
