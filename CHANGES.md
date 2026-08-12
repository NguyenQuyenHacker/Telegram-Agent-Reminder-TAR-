# CHANGES

Lượt dọn dẹp toàn repo. Chỉ đụng vào: đổi tên cho đúng nghĩa, xoá import thừa,
tách hàm dài quá 50 dòng, thêm type hint, thay magic number bằng hằng số đặt tên.
**Không đổi logic nghiệp vụ, không đổi kiến trúc, không thêm thư viện.**

| File | Đã đổi gì |
|------|-----------|
| [app/core/task_code.py](app/core/task_code.py) | Thêm hằng số `_PREFIX_LENGTH`, `_LONGER_PREFIX_LENGTHS`, `_PREFIX_PADDING`, `_SEQ_DIGITS` thay cho các số 2/3/4 rải trong `derive_prefix`, `prefix_alternatives`, `format_code`; thêm kiểu trả về `Iterator[str]` cho `prefix_alternatives`. |
| [app/main.py](app/main.py) | Thêm kiểu trả về: `lifespan -> AsyncIterator[None]`, `health -> dict[str, bool]`. |
| [app/routers/webhooks.py](app/routers/webhooks.py) | Thêm type hint: `graph: Any` cho `_is_awaiting_confirm`/`handle_text_message`, `_thread_config -> dict[str, dict[str, str]]`, `telegram_webhook -> dict[str, bool]`. |
| [app/scheduler/runner.py](app/scheduler/runner.py) | `hour=3` → hằng số `_PURGE_HOUR` (comment giải thích chuyển lên cạnh hằng số). |
| [app/telegram/polling.py](app/telegram/polling.py) | `asyncio.Task` → `asyncio.Task[None]` ở hai chữ ký. |
| [app/telegram/sender.py](app/telegram/sender.py) | Bốn số của chính sách retry → `_MAX_SEND_ATTEMPTS`, `_RETRY_BACKOFF_MULTIPLIER`, `_RETRY_MIN_WAIT_SECONDS`, `_RETRY_MAX_WAIT_SECONDS`. |
| [persistence/proc/checkpoints.py](persistence/proc/checkpoints.py) | Magic number của phép đổi mốc thời gian UUIDv6 → `_UNIX_TO_UUID_EPOCH_SECONDS`, `_TICKS_PER_SECOND`, `_TIME_HIGH_SHIFT`, `_TIME_MID_SHIFT`, `_UUID_VERSION_6`, `_UUID_VARIANT_RFC4122`; tách dòng gộp `hi, mid, lo = ...` thành ba dòng, `lo` → `low`; `s` → `session`. **Đã kiểm chứng chuỗi UUID sinh ra giống hệt bản cũ trên 400 mốc thời gian mẫu.** |
| [persistence/proc/group_codes.py](persistence/proc/group_codes.py) | Thêm kiểu `session: Session` cho `_pick_free_prefix` và `_next_code`; `s` → `session` trong `allocate_code`. |
| [persistence/proc/reports.py](persistence/proc/reports.py) | `limit: int = 10` → `_SEARCH_RESULT_LIMIT`, `source_hash[:16]` → `_REPORT_ID_LENGTH`; `s` → `session`; tách câu `select(...)` dài thành biến `same_hash`. |
| [persistence/proc/tasks.py](persistence/proc/tasks.py) | `limit: int = 5` → `_REFERENCE_MATCH_LIMIT`, `limit: int = 50` → `_QUERY_RESULT_LIMIT`; đổi tên biến session `s` → `session` ở cả 12 hàm; tách câu truy vấn theo mã thành biến `by_code`. |
| [reminder_agent/graph.py](reminder_agent/graph.py) | Tách `build_graph` (56 dòng) thành `_add_report_branch` + `_add_qa_branch`, `build_graph` chỉ còn dựng builder, ghép hai nhánh và gắn điểm vào; thêm `list[AnyMessage]` cho `_recent_history` và tham số `kept_messages` của `_history_deletions`. |
| [reminder_agent/utils/intent.py](reminder_agent/utils/intent.py) | Thêm kiểu trả về `Runnable` cho `_approval_reader`. |

## Không đụng tới

- `tests/`, `TEST/`, `.env`, `requirements.txt`, `reminder_agent/config/models.yaml`,
  `app/core/config.py` (file cấu hình).
- `app/core/datetime_utils.py`, `app/core/priority.py`, `app/core/reminder_digest.py`,
  `app/core/security.py`, `app/core/task_text.py`, `app/telegram/bot.py`,
  `app/telegram/messages.py`, `app/services/reminder_service.py`,
  `persistence/models/*`, `persistence/pool.py`, `reminder_agent/prompts/loader.py`,
  `reminder_agent/config/settings.py`, `reminder_agent/utils/schemas.py`,
  `reminder_agent/utils/state.py`, `reminder_agent/utils/tools/*`, `run.py`
  — đã sạch theo các tiêu chí của lượt này: không có import thừa, không hàm nào
  quá 50 dòng, type hint đã đủ, không có magic number chưa đặt tên.

## Kiểm chứng

- `pytest tests` — **32 passed** (trước khi sửa: 32 passed, không đổi).
- `pyflakes` toàn repo — sạch, không có import thừa hay tên chưa định nghĩa.
- `compileall` toàn bộ `app/`, `persistence/`, `reminder_agent/`, `run.py` — OK.
- Xem thêm [ISSUES.md](ISSUES.md) cho những thứ phát hiện nhưng cố ý không sửa.

---

# Lượt 2 — rà soát đồng bộ trường ở tầng tool

Mục tiêu hẹp: mỗi lệnh ghi phải cập nhật **đủ cụm trường đi cùng nhau**, và cái
tool đọc ra phải khớp cái tin nhắc gộp hiển thị. Lượt này **có đổi logic**.

| File | Đã đổi gì |
|------|-----------|
| [persistence/proc/tasks.py](persistence/proc/tasks.py) | `mark_done`/`cancel_task` gộp vào `_close_task()`: ghi status + mốc của trạng thái mới và **xoá mốc của trạng thái cuối kia**; chỉ nhận dòng `pending`, gật lại đúng trạng thái đang có thì idempotent. `undo_done`/`undo_cancel` gộp vào `_reopen_task()`: mở lại thì xoá **cả hai** mốc. `set_due_date` chỉ nhận dòng `pending`. `upsert_task` bỏ dòng gán `group_id` chết (task_id đã băm từ group_id) và ghi rõ vì sao không dời `next_remind_at`. Thêm `_ilike_pattern()` escape `%`/`_` cho cả hai hàm tìm kiếm. `query_tasks` bỏ tham số `priority`. |
| [reminder_agent/utils/tools/task_view.py](reminder_agent/utils/tools/task_view.py) | **File mới.** `task_brief()` dùng chung cho cả hai tool; `priority` trả về là mức **hiệu lực** (`maybe_escalate`) chứ không phải cột thô. |
| [reminder_agent/utils/tools/query_tasks.py](reminder_agent/utils/tools/query_tasks.py) | Kiểm `status`/`priority`/ngày trước khi tra, sai thì báo lỗi kèm giá trị hợp lệ thay vì ném `ValueError` trần hoặc trả bảng rỗng; chặn khoảng hạn ngược; lọc `priority` theo mức hiệu lực. |
| [reminder_agent/utils/tools/update_task.py](reminder_agent/utils/tools/update_task.py) | Chỉ đọc `new_due_date` khi `action="reschedule"`, ngày gửi kèm `done`/`cancel` bị bỏ tường minh; danh sách ứng viên dùng `task_brief` và **không lộ `task_id`**. |
| [reminder_agent/graph.py](reminder_agent/graph.py) | `_merge_proposals()`: hai đề xuất cho cùng một việc thì giữ cái sau, để bảng xác nhận và thứ được ghi luôn khớp. `_apply_reschedule` không còn gọi `set_due_date(None)` khi thiếu hạn mới (trước đây là **xoá sạch hạn cũ**). |
| [reminder_agent/prompts/agent_system.md](reminder_agent/prompts/agent_system.md) | Nói rõ `priority` trả về là mức đã nâng, LLM không được tự tính lại. |
| [tests/test_tools.py](tests/test_tools.py) | **File mới**, 16 test cho phần thuần của tầng tool. |

## Kiểm chứng lượt 2

- `pytest tests` — **48 passed** (32 cũ + 16 mới).
- `compileall` các file đã đụng — OK.
- **Chưa test được bằng máy:** các guard trong `_close_task` / `_reopen_task` /
  `set_due_date` cần Postgres thật; mới chỉ đọc lại bằng mắt.
