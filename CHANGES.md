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

---

# Lượt 3 — mục 3 và mục 5 của [docs/todo.md](docs/todo.md)

Hai tính năng mới: nhận ảnh / tin nhắn thoại, và chia việc lớn thành việc con
kèm theo dõi tiến độ.

## Mục 5 — việc con và tiến độ

Nguyên tắc: **việc con VẪN là một dòng `task`**, chỉ khác ở `parent_task_id`.
Nhờ vậy báo xong, hủy, đổi hạn, tra theo mã, hoàn tác — tất cả chạy nguyên xi
trên việc con, không phải viết nhánh thứ hai. Chỉ **một tầng**: không gắn việc
con vào việc con.

| File | Đã đổi gì |
|------|-----------|
| [persistence/schema/schema.sql](persistence/schema/schema.sql) | Hai cột mới trên `task`: `parent_task_id` (tự trỏ), `next_sub_seq`. Kèm hai câu `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` để DB dựng từ schema cũ nâng cấp được (`CREATE TABLE IF NOT EXISTS` bỏ qua bảng đã có). Index `idx_task_parent`. |
| [persistence/models/task.py](persistence/models/task.py) | Hai field tương ứng + property `is_subtask`. |
| [app/core/task_code.py](app/core/task_code.py) | `format_subtask_code()`; `_CODE_RE` nhận thêm đuôi `.N` tuỳ chọn nên `parse_code("TB-002.1")` ra `"TB-002.1"` chứ **không** cắt thành `"TB-002"` — cắt là "xong TB-002.1" đóng nhầm cả việc lớn. |
| [persistence/proc/subtasks.py](persistence/proc/subtasks.py) | **File mới.** `add_subtask` / `rename_subtask` / `get_subtasks` / `subtask_progress`. |
| [persistence/proc/tasks.py](persistence/proc/tasks.py) | `get_due_tasks` bỏ qua việc con (chúng không được nhắc riêng). `_close_task` tách ra `_stamp_closed()` và **đóng lây việc con còn chờ** khi đóng việc lớn, trong cùng transaction. `find_tasks_by_reference` thêm tham số `statuses` cho nơi chỉ đọc. |
| [app/telegram/messages.py](app/telegram/messages.py) | `progress_line()` (thanh `▰▱`), `task_detail_text()` (bảng chi tiết), `_due_phrase()` dùng chung cho tin nhắc gộp và bảng chi tiết, `_change_label()` tách khỏi `update_confirm_text`, `subtask_added_text` / `subtask_renamed_text`. `digest_text` nhận thêm `progress` (tuỳ chọn — chữ ký cũ vẫn chạy). |
| [app/services/reminder_service.py](app/services/reminder_service.py) | Đọc tiến độ cả lô một lượt rồi đưa vào `digest_text`. |
| [reminder_agent/utils/tools/task_detail.py](reminder_agent/utils/tools/task_detail.py) | **File mới.** Tool `get_task_detail`. |
| [reminder_agent/utils/tools/update_subtask.py](reminder_agent/utils/tools/update_subtask.py) | **File mới.** Tool `propose_subtask_update` (`add` / `rename`). Xóa việc con thì dùng `propose_task_update(action="cancel")` với mã việc con — không có đường ghi thứ hai. |
| [reminder_agent/utils/tools/task_view.py](reminder_agent/utils/tools/task_view.py) | `task_brief` thêm `is_subtask` và `progress`; `progress` là `null` khi chưa chia nhỏ, khác hẳn `{"done": 0, ...}`. |
| [reminder_agent/utils/tools/query_tasks.py](reminder_agent/utils/tools/query_tasks.py) | Nạp tiến độ cả lô cho các dòng không phải việc con. |
| [reminder_agent/graph.py](reminder_agent/graph.py) | `_UPDATE_ACTIONS` thêm `add_subtask` / `rename_subtask`. `_proposal_key()`: `add_subtask` gộp theo `(task_id, action, nội dung)` — gộp theo `task_id` như ba hành động kia thì "chia thành 4 đầu mục" chỉ còn lại đầu mục cuối. `call_tools` gom thêm `pending_views`, `answer()` gửi bảng chi tiết rồi dọn. |

## Mục 3 — ảnh và tin nhắn thoại

Đường đi: Telegram → tải bytes → một lượt gọi Gemini ra `{intent, text}` → **đi
tiếp đúng đường mà tin nhắn gõ tay đi**. Sau khi có text thì hai loại đầu vào
không còn khác gì nhau, nên graph không phải biết gì về ảnh hay audio.

| File | Đã đổi gì |
|------|-----------|
| [app/telegram/media.py](app/telegram/media.py) | **File mới.** `extract_media()` đọc photo / voice / audio / document ảnh-hoặc-audio; `download_media()` tải về RAM, chặn trước 20MB bằng `getFile`. |
| [reminder_agent/utils/media_text.py](reminder_agent/utils/media_text.py) | **File mới.** `understand_media()`. Dùng part kiểu `{"type": "media", ...}` cho **cả** ảnh lẫn audio — `"image_url"` không mang nổi audio. |
| [reminder_agent/prompts/media_system.md](reminder_agent/prompts/media_system.md) | **File mới.** |
| [reminder_agent/utils/schemas.py](reminder_agent/utils/schemas.py) | `MediaUnderstanding` — trả **cả** `intent` lẫn `text` trong một lượt. Lý do: `classify_message` bắt từ khoá "Tiếp theo:", mà lời thoại "nhớ nộp báo cáo trước thứ 6" không có từ khoá nào — nó sẽ rơi vào nhánh hỏi đáp, nơi **không tool nào tạo được đầu việc**. |
| [reminder_agent/config/models.yaml](reminder_agent/config/models.yaml) | Vai `media` (timeout 60s: file thoại phải tải lên trước khi suy luận). |
| [app/routers/webhooks.py](app/routers/webhooks.py) | Tách `_dispatch` / `_start_report_turn` / `_start_question_turn` khỏi `handle_text_message`; thêm `handle_media_message` và `route_message` (webhook + polling dùng chung). Trả lại cho người dùng thứ bot nghe/đọc được **trước** khi hành động. Trả lời bảng xác nhận bằng tin nhắn thoại cũng chốt được. |
| [app/telegram/polling.py](app/telegram/polling.py) | Gọi `route_message` thay vì chỉ `handle_text_message` — dev local và production không thể lệch nhau. |
| [reminder_agent/prompts/extract_system.md](reminder_agent/prompts/extract_system.md) | Thêm mục ĐẦU VÀO TỰ DO: không có khối "Tiếp theo:" thì trích mọi việc người dùng nhận làm; không nêu dự án nào thì nhóm là `"Việc chung"`. |
| [reminder_agent/prompts/agent_system.md](reminder_agent/prompts/agent_system.md) | Bốn tool thay vì hai; mục SUBTASKS. |
| [persistence/schema/seed_sample.sql](persistence/schema/seed_sample.sql) | TB-001 có 5 việc con (một cái đã hủy, nên số nhảy cóc ở `.2`), đọc ra đúng "Tiến độ: 2/4". |

## Kiểm chứng lượt 3

- `pytest tests` — **86 passed** (48 cũ + 38 mới ở
  [tests/test_subtasks.py](tests/test_subtasks.py),
  [tests/test_media.py](tests/test_media.py),
  [tests/test_task_code.py](tests/test_task_code.py)).
- **Chạy thật trên Postgres**: cấp mã `.1/.2/.3`, thêm trùng nội dung trả về
  chính dòng cũ và không tiêu số thứ tự, chặn lồng hai tầng, `rename` từ chối
  việc lớn, tìm theo mã việc con, tiến độ `1/3`, lượt quét nhắc không lấy việc
  con, đóng việc lớn đóng lây cả ba việc con. Dữ liệu thử đã xoá sạch sau khi
  kiểm.
- **Chạy thật trên Gemini**: đọc ảnh ra text tiếng Việt; caption dạng câu hỏi ra
  `intent="question"`; audio đi qua `inline_data` không lỗi 400, file không có
  tiếng nói trả text rỗng và rơi đúng vào nhánh báo "không nghe rõ".
- Bảng chi tiết và tin nhắc gộp dựng từ seed đúng như mẫu trong `docs/todo.md`.
- **Chưa test được bằng máy:** `download_media()` (cần file thật trên Telegram),
  và toàn bộ lượt hội thoại đi qua LLM (cần chat thật).
